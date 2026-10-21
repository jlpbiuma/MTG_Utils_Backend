import html
import json
import gzip
import zlib
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pydantic import ValidationError

from src.schemas.whatsapp_deals import ManualOpportunityRequest
from src.services.card_link_parser import parse_manabox, parse_moxfield, parse_archidekt
from src.services.manual_opportunity_service import compare_link, validate_link, _fetch, extract_cards, MAX_BYTES


def collection(name, quantity, **kwargs):
    return NS(id=name, cardName=name, quantity=quantity, **kwargs)


def deckcard(name, quantity=1, assigned=0, deck_id="deck-1", **kwargs):
    return NS(cardName=name, quantity=quantity, assignedQuantity=assigned,
              deckId=deck_id, deck=NS(name=deck_id), typeLine=kwargs.get("type_line"))


def fake_db(cards=(), decks=()):
    return NS(deck=NS(find_many=AsyncMock(return_value=[NS(id=id) for id in dict.fromkeys(card.deckId for card in decks)])),
              collectioncard=NS(find_many=AsyncMock(return_value=list(cards))),
              deckcard=NS(find_many=AsyncMock(return_value=list(decks))),
              cardcatalog=NS(find_many=AsyncMock(return_value=[])))


async def compare(database, names, subject="vende"):
    with patch("src.services.whatsapp_deals_service.db", database), patch(
        "src.services.manual_opportunity_service.extract_cards", AsyncMock(return_value=[
            {"name": name, "extra": {"quantity": 2}, "price": "12.34"} for name in names
        ])
    ), patch("src.services.manual_opportunity_service.save_import", AsyncMock(side_effect=lambda user, url, subject, rows, at: rows)):
        return await compare_link("user-1", ManualOpportunityRequest(
            url="https://manabox.app/decks/public", subject=subject))


async def test_sale_matches_missing_decks_without_wants_and_ignores_basics_and_owned_reprints():
    database = fake_db(
        [collection("Owned Card", 2, setCode="OLD"), collection("Partial", 1)],
        [deckcard("Sol Ring", deck_id="commander"), deckcard("Sol Ring", deck_id="sideboard"),
         deckcard("Owned Card", 2), deckcard("Partial", 3),
         deckcard("Forest", 20, type_line="Basic Land — Forest")],
    )
    result = await compare(database, ["Sol Ring", "Owned Card", "Partial", "Forest", "Unneeded"])
    assert result.total_cards_scanned == 5
    assert result.total_wants_matches == 1
    ring = result.wants_matches[0]
    assert ring.matched_want.requested_decks == ["commander", "sideboard"]
    assert ring.matched_want.quantity_wanted == 1
    assert ring.direct_whatsapp_link == ""
    assert ring.price is None  # A catalogue quote is not a seller's offer.
    assert ring.extra["reference_price"] == "12.34"
    database.deckcard.find_many.assert_awaited_once_with(
        where={"deck": {"userId": "user-1", "isArchived": False}}, include={"deck": True})
    database.collectioncard.find_many.assert_awaited_once_with(where={"userId": "user-1"})


async def test_purchase_counts_actual_assignments_across_foil_and_reprints_and_excludes_no_free_copies():
    database = fake_db(
        [collection("Lightning Bolt", 3, isFoil=False), collection("Lightning Bolt", 2, isFoil=True),
         collection("Reserved", 1)],
        [deckcard("Lightning Bolt", quantity=20, assigned=2), deckcard("Reserved", assigned=1)],
    )
    result = await compare(database, ["Lightning Bolt", "Reserved", "Not Owned"], "compra_busca")
    assert result.total_collection_matches == 1
    assert result.collection_matches[0].matched_collection.quantity_owned == 5
    assert result.collection_matches[0].matched_collection.available_quantity == 3
    assert result.total_wants_matches == 0


async def test_split_card_normalization_and_assigned_ownership():
    result = await compare(fake_db([], [deckcard("Fire // Ice", quantity=2, assigned=1)]), ["Fire / Ice"])
    assert result.wants_matches == []  # One assigned copy covers every requested copy.


@pytest.mark.parametrize("url", [
    "http://manabox.app/decks/a", "https://localhost/a", "https://manabox.app.evil.test/a",
    "https://evilmanabox.app/a", "https://user:pass@manabox.app/a", "https://manabox.app:8000/a",
    "https://cardmarket.com/a", "https://[malformed",
])
def test_rejects_unsupported_links(url):
    with pytest.raises(ValueError):
        validate_link(url)


def test_validates_subject():
    with pytest.raises(ValidationError):
        ManualOpportunityRequest(url="https://manabox.app/decks/a", subject="unknown")


def test_parses_worker_formats():
    mox = {"boards": {"mainboard": {"cards": {"id": {
        "quantity": 3, "card": {"name": "Sol Ring", "set": "c21", "prices": {"eur": 1.5}}
    }}}}}
    assert parse_moxfield(mox)[0]["extra"]["quantity"] == 3
    assert parse_moxfield({"mainboard": {"Sol Ring": {"quantity": 1, "card": {"name": "Sol Ring"}}}})[0]["name"] == "Sol Ring"
    props = {"deck": [0, {"cards": [1, [[0, {"name": [0, "Sol Ring"], "quantity": [0, 2]}]]]}]}
    response = httpx.Response(200, text=f'<astro-island props="{html.escape(json.dumps(props), quote=True)}"></astro-island>')
    assert parse_manabox(response)[0]["extra"]["quantity"] == 2
    arch = {"cards": [{"quantity": 4, "card": {"oracleCard": {"name": "Sol Ring"}, "edition": {"editioncode": "c21"}}}]}
    assert parse_archidekt(httpx.Response(200, json=arch))[0]["name"] == "Sol Ring"


PUBLIC_DNS = [(2, 1, 6, "", ("1.1.1.1", 443))]


async def test_fetch_checks_redirect_host_and_private_addresses():
    transport = httpx.MockTransport(lambda request: httpx.Response(302, headers={"location": "https://127.0.0.1/private"}))
    async with httpx.AsyncClient(transport=transport) as client:
        with patch("src.services.manual_opportunity_service.socket.getaddrinfo", return_value=PUBLIC_DNS):
            with pytest.raises(ValueError):
                await _fetch(client, "https://manabox.app/a")
        with patch("src.services.manual_opportunity_service.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]):
            with pytest.raises(ValueError, match="pública"):
                await _fetch(client, "https://manabox.app/a")


async def test_fetch_bounds_content_without_content_length():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * (MAX_BYTES + 1)))
    async with httpx.AsyncClient(transport=transport) as client:
        with patch("src.services.manual_opportunity_service.socket.getaddrinfo", return_value=PUBLIC_DNS):
            with pytest.raises(ValueError, match="tamaño"):
                await _fetch(client, "https://manabox.app/a")


@pytest.mark.parametrize("encoding,compress", [("gzip", gzip.compress), ("deflate", zlib.compress)])
async def test_compressed_manabox_response_is_decoded_once_through_compare_api(encoding, compress):
    from fastapi import FastAPI
    from src.core.auth import get_current_user_id
    from src.routers.whatsapp_deals import router

    url = "https://manabox.app/decks/AZ8iEGCmfJev19xj9ulfyg"
    props = {"deck": [0, {"cards": [1, [[0, {
        "name": [0, "Sol Ring"], "quantity": [0, 2], "setId": [0, "c21"]
    }]]]}]}
    page = f'<astro-island props="{html.escape(json.dumps(props), quote=True)}"></astro-island>'.encode()
    encoded = compress(page)
    def serve(request):
        assert str(request.url) == url
        return httpx.Response(200, content=encoded, headers={
            "content-encoding": encoding, "content-length": str(len(encoded)),
            "content-type": "text/html; charset=utf-8",
        })

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user_id] = lambda: "user-1"
    database = fake_db([], [deckcard("Sol Ring")])
    # Keep extraction, decoding, parsing and matching real; mock only site HTTP,
    # public DNS and the user's database so this regression runs offline.
    site_client = httpx.AsyncClient(transport=httpx.MockTransport(serve))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api_client:
        with patch("src.services.manual_opportunity_service.httpx.AsyncClient", return_value=site_client), \
             patch("src.services.manual_opportunity_service.socket.getaddrinfo", return_value=PUBLIC_DNS), \
             patch("src.services.manual_opportunity_service.save_import", AsyncMock(side_effect=lambda user, url, subject, rows, at: rows)), \
             patch("src.services.whatsapp_deals_service.db", database):
            response = await api_client.post("/api/whatsapp/compare-link", json={"url": url, "subject": "vende"})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total_cards_scanned"] == 1
    assert payload["wants_matches"][0]["card_name"] == "Sol Ring"
    assert payload["wants_matches"][0]["extra"]["quantity"] == 2
    assert payload["wants_matches"][0]["matched_want"]["requested_decks"] == ["deck-1"]


async def test_compressed_response_size_limit_applies_to_decoded_content():
    encoded = gzip.compress(b"x" * (MAX_BYTES + 1))
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, content=encoded, headers={"content-encoding": "gzip", "content-length": str(len(encoded))}))
    async with httpx.AsyncClient(transport=transport) as client:
        with patch("src.services.manual_opportunity_service.socket.getaddrinfo", return_value=PUBLIC_DNS):
            with pytest.raises(ValueError, match="tamaño"):
                await _fetch(client, "https://manabox.app/a")


async def test_empty_or_unreachable_lists_report_errors():
    for side_effect, value in [(None, []), (httpx.ConnectError("unreachable"), None)]:
        with patch("src.services.manual_opportunity_service.extract_cards", AsyncMock(return_value=value, side_effect=side_effect)):
            with pytest.raises(ValueError):
                await compare_link("user-1", ManualOpportunityRequest(url="https://manabox.app/a", subject="vende"))


async def test_moxfield_falls_back_to_v2_and_uses_fixed_api_host():
    responses = [httpx.Response(403, request=httpx.Request("GET", "https://api2.moxfield.com/a")),
                 httpx.Response(200, json={"mainboard": {"Sol Ring": {"card": {"name": "Sol Ring"}}}})]
    async def fetch(client, url):
        response = responses.pop(0)
        response.request = httpx.Request("GET", url)
        response.raise_for_status()
        return response
    with patch("src.services.manual_opportunity_service._fetch", AsyncMock(side_effect=fetch)) as mock:
        assert (await extract_cards("https://moxfield.com/decks/public"))[0]["name"] == "Sol Ring"
    assert mock.await_args_list[1].args[1] == "https://api2.moxfield.com/v2/decks/all/public"


async def test_api_compares_only_for_authenticated_user_and_returns_validation_errors():
    from fastapi import FastAPI, HTTPException
    from src.core.auth import get_current_user_id
    from src.routers.whatsapp_deals import router
    from src.schemas.whatsapp_deals import WhatsAppMatchesResponse

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user_id] = lambda: "authenticated-user"
    payload = {"url": "https://manabox.app/decks/public", "subject": "vende"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        with patch("src.routers.whatsapp_deals.compare_link", AsyncMock(return_value=WhatsAppMatchesResponse())) as compare:
            response = await client.post("/api/whatsapp/compare-link", json={**payload, "user_id": "another-user"})
            assert response.status_code == 200
            assert compare.await_args.args[0] == "authenticated-user"
            response = await client.post("/api/whatsapp/compare-link", json={**payload, "subject": "invalid"})
            assert response.status_code == 422
        with patch("src.routers.whatsapp_deals.compare_link", AsyncMock(side_effect=ValueError("Lista privada"))):
            response = await client.post("/api/whatsapp/compare-link", json=payload)
            assert response.status_code == 400
            assert response.json()["detail"] == "Lista privada"

        def reject_auth():
            raise HTTPException(status_code=401, detail="Unauthorized")

        app.dependency_overrides[get_current_user_id] = reject_auth
        with patch("src.routers.whatsapp_deals.compare_link", AsyncMock()) as compare:
            response = await client.post("/api/whatsapp/compare-link", json=payload)
            assert response.status_code == 401
            compare.assert_not_awaited()


async def test_import_saves_all_cards_including_unmatched_with_import_timestamp():
    from datetime import datetime
    saved_rows = []
    async def save(user, url, subject, rows, at):
        saved_rows.extend([{**row, "id": -index - 1} for index, row in enumerate(rows)])
        return saved_rows
    with patch("src.services.manual_opportunity_service.extract_cards", AsyncMock(return_value=[
        {"name": "Unmatched Card", "extra": {"quantity": 4}},
        {"name": "Sol Ring", "extra": {"quantity": 1}},
    ])), patch("src.services.manual_opportunity_service.save_import", AsyncMock(side_effect=save)) as persist, \
         patch("src.services.whatsapp_deals_service.db", fake_db()):
        response = await compare_link("alice", ManualOpportunityRequest(url="https://manabox.app/decks/public", subject="vende"))
    assert response.total_wants_matches == 0
    assert response.total_cards_scanned == 2
    args = persist.await_args.args
    assert args[:3] == ("alice", "https://manabox.app/decks/public", "vende")
    assert len(args[3]) == 2
    assert isinstance(args[4], datetime) and args[4].utcoffset().total_seconds() == 0
    assert response.last_scraped_at == args[4].isoformat()


async def test_saved_manual_imports_reappear_without_bot_database():
    from src.services.whatsapp_deals_service import WhatsAppDealsService
    row = {"id": -42, "url": "https://manabox.app/decks/public", "card_name": "Sol Ring",
           "set_code": None, "price": None, "currency": None, "condition": None,
           "extra_json": json.dumps({"source": "manual", "quantity": 1}), "subject": "vende",
           "source_phone": "", "source_chat": None, "raw_message": None,
           "detected_at": "2026-10-01T12:30:00+00:00"}
    with patch("src.services.whatsapp_deals_service._get_sqlite_path", return_value=None), \
         patch("src.services.whatsapp_deals_service.read_saved_rows", AsyncMock(return_value=[row])) as read, \
         patch("src.services.whatsapp_deals_service.db", fake_db([], [deckcard("Sol Ring")])):
        first = await WhatsAppDealsService.get_matches("alice")
        reloaded = await WhatsAppDealsService.get_matches("alice")
    assert first == reloaded
    assert reloaded.wants_matches[0].id == -42
    assert reloaded.wants_matches[0].detected_at == row["detected_at"]
    assert reloaded.wants_matches[0].direct_whatsapp_link == ""
    assert reloaded.total_cards_scanned == 1
    assert all(call.args == ("alice",) for call in read.await_args_list)


async def test_compare_response_exposes_local_trend_and_global_completion_independently_of_link_value():
    database = fake_db([], [deckcard("Alpha", 2, deck_id="a"), deckcard("Forest", 8, deck_id="a"),
                            deckcard("Alpha", 1, deck_id="b"), deckcard("Beta", 1, deck_id="b")])
    with patch("src.services.opportunity_valuation.PricingService.get_latest_quotes_batch",
               AsyncMock(return_value={"alpha": NS(unitPrice=NS(trend=3.5))})):
        result = await compare(database, ["Alpha"])
    row = result.wants_matches[0]
    assert row.unit_price == 12.34  # imported catalogue reference is independent of local trend
    assert row.trend_unit_price == 3.5
    assert result.global_deck_count == 2 and result.global_completion_before == 25
    assert row.global_completion_after == 75 and row.completion_gain == 50
    assert len(row.completion_requirements) == 2
    database.deck.find_many.assert_awaited_once_with(where={"userId": "user-1", "isArchived": False})
    serialized = result.model_dump()
    assert serialized["wants_matches"][0]["trend_unit_price"] == 3.5
    assert serialized["global_completion_before"] == 25
