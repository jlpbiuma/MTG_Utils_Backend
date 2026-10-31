"""Resolve exact bilingual catalog names before comparing offers with user cards."""
import json
import time
import logging
import httpx
from types import SimpleNamespace
from src.services.card_utils import is_catalog_record_playable, is_playable_card, normalize_card_name
from src.services.scryfall_service import fold_search_text, _SEARCH_TRANSLATE_FROM, _SEARCH_TRANSLATE_TO, SCRYFALL_BASE, SCRYFALL_HEADERS

# Resolve locally in one batch; exact Spanish lookup handles missing translations.
logger = logging.getLogger(__name__)
def _fold_sql(expression):
    return f"regexp_replace(translate(lower({expression}), '{_SEARCH_TRANSLATE_FROM}', '{_SEARCH_TRANSLATE_TO}'), '[^a-z0-9]+', '', 'g')"


# Independent branches let PostgreSQL use exact expression indexes. The former
# per-row lateral UNION expanded every alias in the entire catalog for each offer.
_RESOLVE_NAMES_SQL = f"""
WITH requested AS MATERIALIZED (SELECT jsonb_array_elements_text($1::jsonb) AS key),
aliases AS (
    SELECT id, name AS alias_name FROM card_catalog
      WHERE {_fold_sql('name')} = ANY(ARRAY(SELECT key FROM requested))
    UNION ALL
    SELECT id, name_es FROM card_catalog
      WHERE {_fold_sql("coalesce(name_es, '')")} = ANY(ARRAY(SELECT key FROM requested))
    UNION ALL
    SELECT id, split_part(name, ' // ', 1) FROM card_catalog
      WHERE strpos(name, ' // ') > 0
        AND {_fold_sql("split_part(name, ' // ', 1)")} = ANY(ARRAY(SELECT key FROM requested))
    UNION ALL
    SELECT id, split_part(name_es, ' // ', 1) FROM card_catalog
      WHERE strpos(name_es, ' // ') > 0
        AND {_fold_sql("split_part(name_es, ' // ', 1)")} = ANY(ARRAY(SELECT key FROM requested))
    UNION ALL
    SELECT id, details_es->>'printed_name' FROM card_catalog
      WHERE {_fold_sql("coalesce(details_es->>'printed_name', '')")} = ANY(ARRAY(SELECT key FROM requested))
    UNION ALL
    SELECT c.id, face->>'printed_name' FROM card_catalog c
      CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(c.details_es->'card_faces') = 'array'
          THEN c.details_es->'card_faces' ELSE '[]'::jsonb END) face
      WHERE jsonb_typeof(c.details_es->'card_faces') = 'array'
        AND {_fold_sql("face->>'printed_name'")} = ANY(ARRAY(SELECT key FROM requested))
)
SELECT DISTINCT c.name, c.type_line, c.set_code, c.collector_number, a.alias_name
FROM aliases a JOIN card_catalog c ON c.id=a.id
"""



async def _fetch_exact_spanish_card(name: str):
    escaped = name.replace('\\', '\\\\').replace('"', '\\"')
    query = f'lang:es game:paper -is:digital name:"{escaped}"'
    try:
        async with httpx.AsyncClient(headers=SCRYFALL_HEADERS, timeout=8.0) as client:
            response = await client.get(f"{SCRYFALL_BASE}/cards/search", params={"q": query, "unique": "cards"})
            if response.status_code == 404:
                return None
            response.raise_for_status()
            matches = {}
            for card in response.json().get('data', []):
                aliases = [card.get('printed_name')]
                aliases.extend(face.get('printed_name') for face in card.get('card_faces') or [])
                if is_playable_card(card) and any(fold_search_text(alias) == fold_search_text(name) for alias in aliases if alias):
                    matches[card['name']] = card
            return next(iter(matches.values())) if len(matches) == 1 else None
    except (httpx.HTTPError, ValueError):
        logger.warning("Could not resolve missing Spanish offer name")
        return None


# Names Scryfall could not resolve, with the monotonic time they may be retried.
# Process-local and only for misses: a found translation is persisted below.
_REMOTE_MISS_TTL_SECONDS = 6 * 3600
_remote_misses: dict[str, float] = {}


async def resolve_offer_names(names: list[str], client, *, allow_remote: bool = True) -> dict[str, str]:
    """Map folded offer names to canonical catalog names.

    ``allow_remote=False`` keeps the lookup local, for listings that must not
    wait on Scryfall; unresolved names are resolved when an offer is opened.
    """
    keys = sorted({fold_search_text(name) for name in names if fold_search_text(name)})
    if not keys:
        return {}
    rows = await client.query_raw(_RESOLVE_NAMES_SQL, json.dumps(keys))
    candidates: dict[str, set[str]] = {}
    for row in rows:
        name, alias = row.get('name'), row.get('alias_name')
        if not isinstance(name, str) or not isinstance(alias, str):
            continue
        if not is_catalog_record_playable(SimpleNamespace(**row)):
            continue
        key = fold_search_text(alias)
        candidates.setdefault(key, set()).add(name)
    # An ambiguous translation remains unresolved rather than matching the wrong card.
    resolved = {key: next(iter(matches)) for key, matches in candidates.items() if len(matches) == 1}
    for name in dict.fromkeys(names):
        key = fold_search_text(name)
        if not key or key in candidates or key in resolved or not allow_remote:
            continue
        if _remote_misses.get(key, 0) > time.monotonic():
            continue
        card = await _fetch_exact_spanish_card(name)
        if card is None:
            _remote_misses[key] = time.monotonic() + _REMOTE_MISS_TTL_SECONDS
            continue
        resolved[key] = card['name']
        printed_name = card.get('printed_name')
        if printed_name:
            # Cache the translation on the canonical catalog row, never on a printing ID.
            await client.cardcatalog.update_many(
                where={'normalizedName': normalize_card_name(card['name']), 'OR': [{'nameEs': None}, {'nameEs': card['name']}]},
                data={'nameEs': printed_name},
            )
    return resolved
