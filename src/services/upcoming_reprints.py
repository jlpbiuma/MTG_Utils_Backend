"""Upcoming paper reprints from local printings and the current bulk snapshot.

Set release dates are authoritative. Reading a bulk preview does not trigger
image downloads, worker enrichment, or live catalog calls on a purchase screen.
"""
import json
import logging
from datetime import date, datetime, timezone
from src.services.card_utils import normalize_card_name
from src.services.set_release_dates import OFFICIAL_RELEASE_CORRECTIONS, corrected_set_release

logger = logging.getLogger(__name__)
_EXCLUDED = {'art_series', 'memorabilia', 'token', 'alchemy', 'minigame'}
_BULK_SQL = """
SELECT DISTINCT b.name_key, s.code AS set_code, s.name AS set_name,
                COALESCE(($3::jsonb->>s.code)::date, s.released_at::date) AS release_date
FROM scryfall_bulk_state snapshot
JOIN scryfall_bulk_cards b ON b.generation=snapshot.generation
JOIN card_sets s ON s.code=b.set_code
WHERE snapshot.kind='cards' AND snapshot.generation IS NOT NULL
  AND b.name_key IN (SELECT jsonb_array_elements_text($1::jsonb))
  AND COALESCE(($3::jsonb->>s.code)::date, s.released_at::date) > $2::date AND NOT s.is_digital
  AND s.set_type NOT IN ('art_series','memorabilia','token','alchemy','minigame')
  AND b.payload->'games' @> '["paper"]'::jsonb
  AND b.payload->>'reprint' = 'true'
  AND COALESCE(b.payload->>'layout','') NOT IN ('art_series','token','double_faced_token','emblem','front_card','minigame')
"""


def release_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            pass
    return None


async def upcoming_reprints_by_name(names, printings, client, today=None):
    today = today or datetime.now(timezone.utc).date()
    keys = {' '.join(name.lower().split()) for name in names if isinstance(name, str) and name.strip()}
    known = set()
    future = []
    for printing in printings:
        catalog = getattr(printing, 'catalog', None)
        name = getattr(catalog, 'name', None) or getattr(catalog, 'normalizedName', None)
        if not isinstance(name, str):
            # The detail endpoint already knows the canonical name.
            name = names[0] if len(names) == 1 else None
        set_obj = getattr(printing, 'set', None)
        if not name or set_obj is None or getattr(set_obj, 'isDigital', False) is True or getattr(set_obj, 'setType', None) in _EXCLUDED:
            continue
        published = release_date(corrected_set_release(getattr(set_obj, 'code', None), getattr(set_obj, 'releasedAt', None))) or release_date(getattr(printing, 'releasedAt', None))
        if published is None:
            continue
        if published <= today:
            known.add(normalize_card_name(name))
        else:
            future.append((name, set_obj, published))
    result = {}
    def add(name, code, title, published):
        if not isinstance(name, str) or not isinstance(code, str) or not isinstance(title, str) or published is None or published <= today:
            return
        result.setdefault(normalize_card_name(name), {})[code.lower()] = {
            'setCode': code.lower(), 'setName': title, 'releaseDate': published.isoformat(),
        }
    for name, set_obj, published in future:
        if normalize_card_name(name) in known:
            add(name, getattr(set_obj, 'code', None), getattr(set_obj, 'name', None), published)
    if keys:
        try:
            rows = await client.query_raw(_BULK_SQL, json.dumps(sorted(keys)), today.isoformat(), json.dumps(OFFICIAL_RELEASE_CORRECTIONS))
            for row in rows:
                add(row.get('name_key'), row.get('set_code'), row.get('set_name'), release_date(row.get('release_date')))
        except Exception as error:
            # Older installations without an activated bulk snapshot can use local prints.
            logger.warning('Upcoming bulk reprints unavailable: %s', type(error).__name__)
    return {name: sorted(sets.values(), key=lambda item: (item['releaseDate'], item['setCode'])) for name, sets in result.items()}
