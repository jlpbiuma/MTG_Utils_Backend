import pytest
import sqlite3
from unittest.mock import patch, MagicMock

from src.services.whatsapp_deals_service import WhatsAppDealsService


@pytest.mark.asyncio
async def test_whatsapp_deals_matching_flow(tmp_path):
    # 1. Crear SQLite temporal con esquema de worker_whatsapp
    db_file = str(tmp_path / "test_whatsapp_deals.db")
    conn = sqlite3.connect(db_file)
    conn.execute("""
    CREATE TABLE detected_links (
        url TEXT PRIMARY KEY,
        source_chat TEXT,
        source_phone TEXT,
        subject TEXT,
        raw_message TEXT,
        detected_at TEXT,
        link_status TEXT DEFAULT 'valid',
        scrape_status TEXT DEFAULT 'done',
        checked_at TEXT,
        scraped_at TEXT
    );
    """)
    conn.execute("""
    CREATE TABLE extracted_cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT,
        card_name TEXT,
        set_code TEXT,
        price TEXT,
        currency TEXT,
        condition TEXT,
        extra_json TEXT,
        extracted_at TEXT
    );
    """)

    # Enlace 1: Vendedor vendiendo Sol Ring a 1.50€
    conn.execute(
        "INSERT INTO detected_links (url, source_phone, subject, link_status) VALUES (?, ?, ?, ?)",
        ("https://manabox.app/decks/seller1", "34611111111", "vende", "valid"),
    )
    conn.execute(
        "INSERT INTO extracted_cards (url, card_name, set_code, price, currency) VALUES (?, ?, ?, ?, ?)",
        ("https://manabox.app/decks/seller1", "Sol Ring", "C21", "1.50", "EUR"),
    )

    # Enlace 2: Comprador buscando Lightning Bolt
    conn.execute(
        "INSERT INTO detected_links (url, source_phone, subject, link_status) VALUES (?, ?, ?, ?)",
        ("https://manabox.app/decks/buyer1", "34622222222", "compra_busca", "valid"),
    )
    conn.execute(
        "INSERT INTO extracted_cards (url, card_name, set_code, price, currency) VALUES (?, ?, ?, ?, ?)",
        ("https://manabox.app/decks/buyer1", "Lightning Bolt", "CLB", None, "EUR"),
    )

    conn.commit()
    conn.close()

    # 2. Mockear DB de Prisma (PostgreSQL) para simular wants y colección
    want_mock = MagicMock()
    want_mock.id = "want-1"
    want_mock.cardName = "Sol Ring"
    want_mock.quantity = 2

    col_mock = MagicMock()
    col_mock.id = "col-1"
    col_mock.cardName = "Lightning Bolt"
    col_mock.quantity = 4

    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    fake_db = SimpleNamespace(
        wantcard=SimpleNamespace(find_many=AsyncMock(return_value=[want_mock])),
        collectioncard=SimpleNamespace(find_many=AsyncMock(return_value=[col_mock])),
        deckcard=SimpleNamespace(find_many=AsyncMock(return_value=[])),
        cardcatalog=SimpleNamespace(find_many=AsyncMock(return_value=[])),
    )

    with patch("src.services.whatsapp_deals_service.db", new=fake_db), \
         patch("src.services.whatsapp_deals_service.WantService._deck_demand_by_name", return_value={}):

        response = await WhatsAppDealsService.get_matches("user-123", custom_db_path=db_file)

    # 3. Comprobaciones de resultados
    assert response.total_wants_matches == 1
    assert response.total_collection_matches == 1

    # Comprobación de Sol Ring (Wants vs Venta)
    want_match = response.wants_matches[0]
    assert want_match.card_name == "Sol Ring"
    assert want_match.price == "1.50"
    assert want_match.subject == "vende"
    assert want_match.matched_want.quantity_wanted == 2
    assert "wa.me/34611111111" in want_match.direct_whatsapp_link
    import urllib.parse
    assert "Sol Ring" in urllib.parse.unquote(want_match.direct_whatsapp_link)

    # Comprobación de Lightning Bolt (Colección vs Compra/Busca)
    col_match = response.collection_matches[0]
    assert col_match.card_name == "Lightning Bolt"
    assert col_match.subject == "compra_busca"
    assert col_match.matched_collection.quantity_owned == 4
    assert col_match.matched_collection.available_quantity == 4
    assert "wa.me/34622222222" in col_match.direct_whatsapp_link
    assert "Lightning Bolt" in urllib.parse.unquote(col_match.direct_whatsapp_link)
