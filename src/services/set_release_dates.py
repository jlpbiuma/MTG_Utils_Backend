"""Verified publisher corrections where upstream set metadata is stale."""
from datetime import datetime, timezone

# Wizards distinguishes Foundations (FDN, 2024) from Foundations Commander (FDC).
# https://www.magic.wizards.com/en/news/announcements/foundations-commander-decklists
OFFICIAL_RELEASE_CORRECTIONS = {'fdc': '2026-10-02'}


def corrected_set_release(code, original):
    corrected = OFFICIAL_RELEASE_CORRECTIONS.get(code.lower()) if isinstance(code, str) else None
    return datetime.fromisoformat(corrected).replace(tzinfo=timezone.utc) if corrected else original
