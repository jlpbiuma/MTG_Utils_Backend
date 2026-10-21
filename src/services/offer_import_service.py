"""Promote a public list or discovered source into an editable, user-owned offer."""
import json
from prisma import Json
from src.core.db import db
from src.schemas.whatsapp_deals import ManualOpportunityRequest
from src.services.manual_opportunity_service import compare_link
from src.services.manual_opportunity_store import read_saved_rows
from src.services.whatsapp_deals_service import WhatsAppDealsService, _get_sqlite_path
from src.services.simulated_collection_service import SimulatedCollectionService


async def import_link_offer(user_id, payload):
    url = payload.url.strip()
    if payload.savedOnly:
        rows = await read_saved_rows(user_id)
        rows = [row for row in rows if row["url"] == url and row["subject"] == payload.subject]
        if not rows:
            path = _get_sqlite_path()
            if path:
                automatic, _, _ = WhatsAppDealsService.read_automatic_rows(path)
                rows = [dict(row) for row in automatic if row["url"] == url and row["subject"] == payload.subject]
    else:
        await compare_link(user_id, ManualOpportunityRequest(url=url, subject=payload.subject))
        rows = [row for row in await read_saved_rows(user_id) if row["url"] == url and row["subject"] == payload.subject]
    if not rows:
        raise ValueError("La lista ya no está disponible. Importa su enlace de nuevo.")
    existing = await db.simulatedcollection.find_first(where={"userId": user_id, "sourceUrl": url, "opportunitySubject": payload.subject})
    if existing:
        return await SimulatedCollectionService.get_simulated_collection(user_id, existing.id)
    analysis = await SimulatedCollectionService.create_simulated_collection(
        user_id, payload.name or ("Lista para vender" if payload.subject == "compra_busca" else "Oferta importada"),
        None, source_rows_text(rows), origin=payload.origin,
    )
    await db.simulatedcollection.update(where={"id": analysis.id}, data={
        "sourceUrl": url, "opportunitySubject": payload.subject,
        "sourceMetadata": Json({"rows": rows}),
    })
    return await SimulatedCollectionService.get_simulated_collection(user_id, analysis.id)


def source_rows_text(rows):
    lines = []
    for row in rows:
        extra = json.loads(row.get("extra_json") or "{}")
        quantity = extra.get("quantity", 1)
        quantity = max(1, int(quantity)) if isinstance(quantity, (int, float)) else 1
        set_code = row.get("set_code")
        collector = extra.get("collector_number")
        edition = f" ({set_code}) {collector or ''}" if set_code else ""
        lines.append(f"{quantity} {row['card_name']}{edition}")
    return "\n".join(lines)


async def discover_offers(user_id):
    from src.services.import_service import parse_decklist_text
    from types import SimpleNamespace
    from src.schemas.simulated_collections import SimulatedCollectionSummary
    from datetime import datetime, timezone
    matches = await WhatsAppDealsService.get_matches(user_id)
    relevant = {(match.whatsapp_url, match.subject) for match in matches.wants_matches + matches.collection_matches}
    saved = await db.simulatedcollection.find_many(where={"userId": user_id})
    existing = {(offer.sourceUrl, offer.opportunitySubject) for offer in saved}
    rows = await read_saved_rows(user_id)
    path = _get_sqlite_path()
    if path:
        automatic, _, _ = WhatsAppDealsService.read_automatic_rows(path)
        rows += [dict(row) for row in automatic]
    grouped = {}
    for row in rows:
        key = row["url"], row["subject"]
        if key in relevant and key not in existing:
            grouped.setdefault(key, []).append(row)
    summaries = []
    names = [entry.name for source in grouped.values() for entry in parse_decklist_text(source_rows_text(source))]
    context = await SimulatedCollectionService._prepare_context(user_id, names) if grouped else None
    for (url, subject), source in grouped.items():
        first = source[0]
        origin = "other" if json.loads(first.get("extra_json") or "{}").get("source") == "manual" else "whatsapp"
        name = first.get("source_chat") or ("Oferta detectada" if subject == "vende" else "Demanda de compra")
        analysis = await SimulatedCollectionService.analyze_raw_text(user_id, source_rows_text(source), name=name, include_history=False, context=context)
        analysis.id = "discovery:" + json.dumps([subject, url], ensure_ascii=False)
        analysis.origin, analysis.opportunitySubject, analysis.sourceUrl = origin, subject, url
        metadata = SimpleNamespace(sourceMetadata={"rows": source})
        await SimulatedCollectionService._attach_opportunities(user_id, analysis, metadata, context=context)
        timestamp = first.get("detected_at") or datetime.now(timezone.utc).isoformat()
        fields = analysis.model_dump()
        summaries.append(SimulatedCollectionSummary(**fields, createdAt=timestamp, updatedAt=timestamp))
    return summaries
