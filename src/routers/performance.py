from fastapi import APIRouter, Depends, HTTPException
from src.core.auth import get_current_user_id
from src.core.db import db

router = APIRouter(tags=['performance'])

async def lot_values(user_id, code=None):
    return await db.query_raw('''SELECT b.id,b.kind,b.currency,b.acquired_on::text AS "acquiredOn",b.notes,
      (b.cost_total+b.shipping+b.fee)::float AS investment,
      COALESCE(SUM(CASE WHEN i.is_foil THEN p.price_eur_foil ELSE COALESCE(p.price_cardmarket_trend,p.price_eur) END*i.quantity),0)::float AS "marketValue",
      COALESCE(SUM(CASE WHEN (CASE WHEN i.is_foil THEN p.price_eur_foil ELSE COALESCE(p.price_cardmarket_trend,p.price_eur) END)>=0.25
        THEN (CASE WHEN i.is_foil THEN p.price_eur_foil ELSE COALESCE(p.price_cardmarket_trend,p.price_eur) END)*i.quantity ELSE 0 END),0)::float AS "sellableValue",
      COALESCE(SUM(i.quantity),0)::int AS copies,
      COUNT(*) FILTER(WHERE i.printing_id IS NOT NULL AND (CASE WHEN i.is_foil THEN p.price_eur_foil ELSE COALESCE(p.price_cardmarket_trend,p.price_eur) END) IS NULL)::int AS "unpricedItems"
      FROM acquisition_batches b LEFT JOIN acquisition_batch_items i ON i.batch_id=b.id
      LEFT JOIN card_printings p ON p.id=i.printing_id LEFT JOIN card_sets s ON s.id=p.set_id
      WHERE b.user_id=$1 AND b.annulled_at IS NULL AND ($2::text IS NULL OR lower(b.set_code)=lower($2)
       OR EXISTS(SELECT 1 FROM acquisition_batch_items bi JOIN card_printings bp ON bp.id=bi.printing_id JOIN card_sets bs ON bs.id=bp.set_id WHERE bi.batch_id=b.id AND bs.code=lower($2)))
      GROUP BY b.id ORDER BY b.acquired_on DESC''', user_id, code)

@router.get('/expansions/{code}/performance')
async def performance(code: str, user_id: str = Depends(get_current_user_id)):
    card_set = await db.cardset.find_unique(where={'code': code.lower()})
    if not card_set:
        raise HTTPException(404, 'Edición no encontrada')
    lots = await lot_values(user_id, code)
    index = await db.query_raw('SELECT captured_on::text AS date,index_value AS value,n_cards AS "nCards" FROM set_price_index WHERE set_code=lower($1) AND provider=\'cardmarket\' AND definition_version=1 ORDER BY captured_on', code)
    baseline = await db.query_raw('SELECT MIN(baseline_date)::text AS date FROM set_index_members WHERE set_code=lower($1) AND definition_version=1', code)
    for lot in lots:
        lot['roi'] = round((lot['marketValue']/lot['investment']-1)*100, 2) if lot['investment']>0 and lot['currency']=='EUR' and not lot['unpricedItems'] and lot['copies'] else None
        lot['points'] = await db.query_raw('''WITH dates AS (SELECT DISTINCT date_trunc('week',h.date)::date+6 AS day
          FROM cm_price_history h JOIN acquisition_batch_items i ON i.printing_id=h.scryfall_id
          WHERE i.batch_id=$1 AND h.date>=$2::date AND h.finish=CASE WHEN i.is_foil THEN 1 ELSE 0 END),
          valuations AS (SELECT LEAST(d.day,CURRENT_DATE) AS day,i.quantity,current.price_cents,baseline.price_cents AS base
          FROM dates d JOIN acquisition_batch_items i ON i.batch_id=$1
          LEFT JOIN LATERAL (SELECT price_cents FROM cm_price_history h WHERE h.scryfall_id=i.printing_id
            AND h.finish=CASE WHEN i.is_foil THEN 1 ELSE 0 END AND h.date<=LEAST(d.day,CURRENT_DATE) ORDER BY date DESC LIMIT 1) current ON true
          LEFT JOIN LATERAL (SELECT price_cents FROM cm_price_history h WHERE h.scryfall_id=i.printing_id
            AND h.finish=CASE WHEN i.is_foil THEN 1 ELSE 0 END AND h.date<=$2::date ORDER BY date DESC LIMIT 1) baseline ON true)
          SELECT day::text AS date,SUM(price_cents*quantity)::float/NULLIF(SUM(base*quantity),0)*100 AS value
          FROM valuations GROUP BY day HAVING COUNT(price_cents)=COUNT(*) AND COUNT(base)=COUNT(*) ORDER BY day''', lot['id'], lot['acquiredOn'])
    owned_series = await db.query_raw("""WITH holdings AS (
      SELECT resolved.id, c.is_foil, SUM(c.quantity)::int AS quantity FROM user_collections c
      JOIN LATERAL (SELECT p.id FROM card_printings p JOIN card_sets s ON s.id=p.set_id
        WHERE s.code=lower($2) AND (p.id=c.card_scryfall_id OR (lower(c.set_code)=s.code AND lower(c.collector_number)=lower(p.collector_number)))
        ORDER BY (p.id=c.card_scryfall_id) DESC LIMIT 1) resolved ON true
      WHERE c.user_id=$1 GROUP BY resolved.id,c.is_foil),
      dates AS (SELECT DISTINCT date_trunc('week',h.date)::date+6 AS day FROM holdings i JOIN cm_price_history h ON h.scryfall_id=i.id AND h.finish=CASE WHEN i.is_foil THEN 1 ELSE 0 END),
      values_by_day AS (SELECT LEAST(d.day,CURRENT_DATE) AS day,i.quantity,h.price_cents FROM dates d CROSS JOIN holdings i
        LEFT JOIN LATERAL (SELECT price_cents FROM cm_price_history WHERE scryfall_id=i.id AND finish=CASE WHEN i.is_foil THEN 1 ELSE 0 END
         AND date<=LEAST(d.day,CURRENT_DATE) ORDER BY date DESC LIMIT 1) h ON true),
      complete AS (SELECT day,SUM(price_cents*quantity)/100.0 AS value FROM values_by_day GROUP BY day HAVING COUNT(price_cents)=COUNT(*))
      SELECT day::text AS date,value::float AS "marketValue",value::float/NULLIF(first_value(value) OVER (ORDER BY day),0)*100 AS value
      FROM complete ORDER BY day""", user_id, code)
    return {'setCode': code, 'releasedAt': card_set.releasedAt, 'baselineDate': baseline[0]['date'] if baseline else None,
      'definitionVersion': 1, 'provider': 'cardmarket', 'index': index, 'lots': lots, 'ownedSeries': owned_series,
      'historyNote': 'Cohorte fija de impresiones de ediciones no promocionales con precio inicial > 0,25 €. Base: primera observación disponible desde la salida; no se reconstruyen precios ausentes. Las curvas de lote y colección revalorizan su composición actual; no representan entradas y salidas históricas. La curva de colección usa como base la primera observación completa. Valor vendible: umbral de 0,25 €, sin comisiones ni garantía de venta.'}

@router.get('/expansions/{code}/cards')
async def card_performance(code: str, user_id: str = Depends(get_current_user_id)):
    return await db.query_raw('''SELECT p.id,cat.name AS "cardName",COALESCE(p.price_cardmarket_trend,p.price_eur)::float AS "currentPrice",
     first.price_cents/100.0 AS "baselinePrice",first.date::text AS "baselineDate",peak.price/100.0 AS "peakPrice",
     CASE WHEN first.price_cents>0 THEN (COALESCE(p.price_cardmarket_trend,p.price_eur)/(first.price_cents/100.0)-1)*100 END AS "changePercent",
     (SELECT COALESCE(json_agg(json_build_object('id',d.id,'name',d.name)), '[]'::json) FROM decks d WHERE d.user_id=$2 AND NOT d.is_archived
       AND EXISTS(SELECT 1 FROM deck_cards dc WHERE dc.deck_id=d.id AND (dc.card_scryfall_id=p.id OR lower(dc.card_name)=lower(cat.name)))) AS decks
     FROM card_printings p JOIN card_sets s ON s.id=p.set_id LEFT JOIN card_catalog cat ON cat.id=p.catalog_id
     LEFT JOIN LATERAL(SELECT price_cents,date FROM cm_price_history h WHERE h.scryfall_id=p.id AND h.finish=0 AND h.date>=s.released_at::date ORDER BY date LIMIT 1) first ON true
     LEFT JOIN LATERAL(SELECT MAX(price_cents)::float AS price FROM cm_price_history h WHERE h.scryfall_id=p.id AND h.finish=0) peak ON true
     WHERE s.code=lower($1) ORDER BY p.collector_number''', code, user_id)

@router.get('/decks/{deck_id}/value-history')
async def deck_value_history(deck_id: str, user_id: str = Depends(get_current_user_id)):
    deck = await db.deck.find_unique(where={'id': deck_id})
    if not deck or deck.userId!=user_id:
        raise HTTPException(404, 'Mazo no encontrado')
    points = await db.query_raw('''WITH resolved AS (
       SELECT dc.quantity,p.id FROM deck_cards dc JOIN LATERAL (
         SELECT cp.id FROM card_printings cp JOIN card_sets s ON s.id=cp.set_id
         WHERE cp.id=dc.card_scryfall_id OR cp.catalog_id=dc.card_scryfall_id
         ORDER BY (cp.id=dc.card_scryfall_id) DESC LIMIT 1) p ON true WHERE dc.deck_id=$1 AND NOT dc.is_sideboard),
       dates AS (SELECT DISTINCT date FROM cm_price_history WHERE date>=CURRENT_DATE-30),
       vals AS (SELECT d.date,r.quantity,h.price_cents FROM dates d CROSS JOIN resolved r LEFT JOIN LATERAL (
         SELECT price_cents FROM cm_price_history WHERE scryfall_id=r.id AND finish=0 AND date<=d.date ORDER BY date DESC LIMIT 1) h ON true)
       SELECT date::text AS date,SUM(price_cents*quantity)/100.0 AS value FROM vals GROUP BY date
       HAVING COUNT(price_cents)=COUNT(*) ORDER BY date''', deck_id)
    return {'points': points, 'note': 'Valor de la composición actual del mazo. Cardmarket, no foil, sin sideboard. Se omiten fechas con precios incompletos.'}
