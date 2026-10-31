-- Transform, modal DFC and battle cards only carry their mana cost per face in
-- Scryfall. Rows stored before the worker read faces have no cost, so their
-- mana value is 0 and their colors are missing. Data-only and idempotent: it
-- fills empty costs from the front face and never overwrites a stored value.
UPDATE card_catalog
SET mana_cost = details_es->'card_faces'->0->>'mana_cost'
WHERE COALESCE(mana_cost, '') = ''
  AND jsonb_typeof(details_es->'card_faces') = 'array'
  AND COALESCE(details_es->'card_faces'->0->>'mana_cost', '') <> '';
-- statement-break
UPDATE card_catalog
SET details_es = jsonb_set(details_es, '{mana_cost}', to_jsonb(mana_cost))
WHERE COALESCE(mana_cost, '') <> ''
  AND jsonb_typeof(details_es) = 'object'
  AND COALESCE(details_es->>'mana_cost', '') = '';
-- statement-break
-- User rows copy the catalog cost; resolve through the printing first, then a
-- catalog id stored directly as the card id.
UPDATE deck_cards d SET mana_cost = c.mana_cost
FROM card_printings p JOIN card_catalog c ON c.id = p.catalog_id
WHERE p.id = d.card_scryfall_id AND COALESCE(d.mana_cost, '') = '' AND COALESCE(c.mana_cost, '') <> '';
-- statement-break
UPDATE deck_cards d SET mana_cost = c.mana_cost
FROM card_catalog c
WHERE c.id = d.card_scryfall_id AND COALESCE(d.mana_cost, '') = '' AND COALESCE(c.mana_cost, '') <> '';
-- statement-break
UPDATE user_collections u SET mana_cost = c.mana_cost
FROM card_printings p JOIN card_catalog c ON c.id = p.catalog_id
WHERE p.id = u.card_scryfall_id AND COALESCE(u.mana_cost, '') = '' AND COALESCE(c.mana_cost, '') <> '';
-- statement-break
UPDATE user_collections u SET mana_cost = c.mana_cost
FROM card_catalog c
WHERE c.id = u.card_scryfall_id AND COALESCE(u.mana_cost, '') = '' AND COALESCE(c.mana_cost, '') <> '';
-- statement-break
UPDATE user_wants w SET mana_cost = c.mana_cost
FROM card_printings p JOIN card_catalog c ON c.id = p.catalog_id
WHERE p.id = w.card_scryfall_id AND COALESCE(w.mana_cost, '') = '' AND COALESCE(c.mana_cost, '') <> '';
-- statement-break
UPDATE user_wants w SET mana_cost = c.mana_cost
FROM card_catalog c
WHERE c.id = w.card_scryfall_id AND COALESCE(w.mana_cost, '') = '' AND COALESCE(c.mana_cost, '') <> '';
-- statement-break
UPDATE simulated_cards s SET mana_cost = c.mana_cost
FROM card_printings p JOIN card_catalog c ON c.id = p.catalog_id
WHERE p.id = s.card_scryfall_id AND COALESCE(s.mana_cost, '') = '' AND COALESCE(c.mana_cost, '') <> '';
