"""Import a public card list and compare it with its owner's decks and collection."""
import asyncio
import ipaddress
import json
import socket
from datetime import datetime, timezone
from urllib.parse import urlparse, urljoin

import httpx

from src.schemas.whatsapp_deals import ManualOpportunityRequest
from src.services.card_link_parser import parse_archidekt, parse_manabox, parse_moxfield
from src.services.whatsapp_deals_service import WhatsAppDealsService
from src.services.manual_opportunity_store import save_import

ALLOWED_HOSTS = {
    "manabox.app", "www.manabox.app", "moxfield.com", "www.moxfield.com",
    "api.moxfield.com", "api2.moxfield.com", "archidekt.com", "www.archidekt.com",
}
MAX_BYTES = 3 * 1024 * 1024


def validate_link(url: str) -> str:
    try:
        parsed = urlparse(url)
        if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS
                or parsed.username or parsed.password or parsed.port not in (None, 443)):
            raise ValueError
    except ValueError:
        raise ValueError("Introduce un enlace HTTPS de ManaBox, Moxfield o Archidekt.") from None
    return parsed.hostname


async def _fetch(client: httpx.AsyncClient, url: str) -> httpx.Response:
    for _ in range(4):
        host = validate_link(url)
        addresses = await asyncio.to_thread(socket.getaddrinfo, host, 443)
        if not addresses or any(not ipaddress.ip_address(entry[4][0]).is_global for entry in addresses):
            raise ValueError("El enlace no apunta a una dirección pública.")
        async with client.stream("GET", url) as response:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("El enlace contiene una redirección inválida.")
                url = urljoin(url, location)
                continue
            response.raise_for_status()
            chunks = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError("La lista supera el tamaño máximo permitido.")
                chunks.append(chunk)
            # aiter_bytes() has already decoded gzip/deflate content. Keeping the
            # wire encoding headers makes the new Response decode it a second
            # time, raising DecodingError even though the site returned 200.
            headers = {key: value for key, value in response.headers.items()
                       if key.lower() not in {"content-encoding", "content-length", "transfer-encoding"}}
            return httpx.Response(response.status_code, headers=headers,
                                  content=b"".join(chunks), request=response.request)
    raise ValueError("El enlace contiene demasiadas redirecciones.")


async def extract_cards(url: str) -> list[dict]:
    host = validate_link(url)
    path = urlparse(url).path
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.moxfield.com/"}
    async with httpx.AsyncClient(timeout=15, follow_redirects=False, headers=headers) as client:
        if host in {"moxfield.com", "www.moxfield.com", "api.moxfield.com", "api2.moxfield.com"}:
            import re
            match = re.fullmatch(r"/(?:decks|v[23]/decks/all)/([A-Za-z0-9_-]+)/?", path)
            if not match:
                raise ValueError("Introduce el enlace de una lista pública de Moxfield.")
            for version in ("v3", "v2"):
                try:
                    response = await _fetch(client, f"https://api2.moxfield.com/{version}/decks/all/{match[1]}")
                    cards = parse_moxfield(response.json())
                    if cards:
                        return cards
                except (httpx.HTTPStatusError, json.JSONDecodeError):
                    continue
            raise ValueError("Moxfield no permitió leer la lista. Comprueba que sea pública.")
        response = await _fetch(client, url)
        return parse_manabox(response) if "manabox" in host else parse_archidekt(response)


async def compare_link(user_id: str, request: ManualOpportunityRequest):
    url = request.url.strip()
    try:
        cards = await asyncio.wait_for(extract_cards(url), timeout=45)
    except (httpx.HTTPError, OSError, TimeoutError) as exc:
        raise ValueError("No se pudo leer el enlace. Comprueba que sea público e inténtalo de nuevo.") from exc
    if not cards:
        raise ValueError("No se encontraron cartas en el enlace. Comprueba que la lista sea pública.")
    imported_at = datetime.now(timezone.utc)
    now = imported_at.isoformat()
    rows = [{
        "id": index, "url": url, "card_name": card.get("name"),
        "set_code": card.get("set_code"),
        # Site prices describe catalogue values, not a seller's asking price.
        "price": None, "currency": card.get("currency"), "condition": None,
        "extra_json": json.dumps({**card.get("extra", {}), "source": "manual",
                                   "reference_price": card.get("price")}),
        "subject": request.subject, "source_phone": "", "source_chat": None,
        "raw_message": None, "detected_at": now,
    } for index, card in enumerate(cards) if card.get("name")]
    if not rows:
        raise ValueError("No se encontraron cartas en el enlace.")
    rows = await save_import(user_id, url, request.subject, rows, imported_at)
    now = rows[0]["detected_at"]
    return await WhatsAppDealsService.match_rows(
        user_id, rows, total_scanned=len(rows), last_scraped=now, manual=True,
    )
