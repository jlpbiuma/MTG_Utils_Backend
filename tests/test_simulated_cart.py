import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as NS
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from src.services.cardmarket_cart_parser import parse_cardmarket_cart, CartEntry, CartImport
from src.services.simulated_purchase_service import compare_purchase
from src.services.simulated_collection_service import SimulatedCollectionService
from src.services.card_utils import normalize_card_name

FIXTURES = Path(__file__).parent / 'fixtures' / 'cardmarket'
EXPECTED = json.loads((FIXTURES / 'expected.json').read_text())


def printing(name, price, number='1', set_name='Example', foil=None):
    return NS(id=f'{name}-{set_name}-{number}', collectorNumber=number,
              catalog=NS(normalizedName=normalize_card_name(name), typeLine='Creature', manaCost=None),
              set=NS(name=set_name, code='tst'), priceCardmarketTrend=price, priceEur=None,
              priceEurFoil=foil, imageUri=None, imageUriLarge=None, imageUriSmall=None)


def database(printings=(), wants=()):
    return NS(deck=NS(find_many=AsyncMock(return_value=[])),
              collectioncard=NS(find_many=AsyncMock(return_value=[])),
              cardprinting=NS(find_many=AsyncMock(return_value=list(printings))),
              wantcard=NS(find_many=AsyncMock(return_value=list(wants))),
              simulatedcollection=NS(create=AsyncMock(), find_unique=AsyncMock(), find_many=AsyncMock(), update=AsyncMock()),
              simulatedcard=NS(create=AsyncMock(), delete_many=AsyncMock()))


@pytest.mark.parametrize('filename', EXPECTED)
async def test_full_user_clipboard_fixtures(filename):
    raw = (FIXTURES / filename).read_text()
    expected = EXPECTED[filename]
    cart = parse_cardmarket_cart(raw)
    assert cart is not None
    assert cart.warnings == []
    assert len(cart.entries) == expected['count']
    assert [c.name for c in cart.entries] == expected['names']
    assert sum(c.unit_price * c.quantity for c in cart.entries) == Decimal(expected['total'])
    assert all(c.quantity == 1 and c.collector_number for c in cart.entries)
    assert not any(c.is_foil for c in cart.entries)  # MF / 0852F are seller comments.
    db = database()
    with patch('src.services.simulated_collection_service.db', db):
        analysis = await SimulatedCollectionService.analyze_raw_text('user-a', raw)
    assert analysis.totalCards == expected['count']
    assert analysis.uniqueCards == len(set(expected['names']))
    assert analysis.purchaseAnalysis.totalPurchaseCost == float(expected['total'])
    assert analysis.purchaseAnalysis.missingMarketPriceCopies == expected['count']
    assert analysis.purchaseAnalysis.savings is None
    assert analysis.rawText == raw
    db.wantcard.find_many.assert_awaited_once_with(where={'userId': 'user-a'})
    db.collectioncard.find_many.assert_awaited_once_with(where={'userId': 'user-a'})
    db.deck.find_many.assert_awaited_once_with(where={'userId': 'user-a', 'isArchived': False}, include={'cards': True})
    db.simulatedcollection.create.assert_not_awaited()
    if filename == 'cart-markdown.txt':
        brokers = [c for c in cart.entries if c.name == 'Brokers Charm']
        assert [c.collector_number for c in brokers] == ['298', '171']
        assert [c.unit_price for c in brokers] == [Decimal('.20'), Decimal('.15')]
        assert analysis.uniqueCards == 25


def test_wants_caps_categories_and_purchase_vs_market_arithmetic():
    cart = CartImport(entries=[
        CartEntry('A', 3, Decimal('2'), '1', 'Example'),
        CartEntry('A', 2, Decimal('3')),
        CartEntry('B', 1, Decimal('1')),
        CartEntry('C', 1, Decimal('10')),
        CartEntry('D', 1, Decimal('.5')),
        CartEntry('E', 1, Decimal('4')),
    ])
    result = compare_purchase(cart, [printing('A', 4), printing('A', 1, set_name='Other'),
                                    printing('B', .5), printing('C', 5), printing('D', 1)],
                              [NS(cardName='A', quantity=2), NS(cardName='C', quantity=1)], {'b': 1}, {'a'})
    assert result.totalPurchaseCost == 27.5
    assert result.comparedPurchaseCost == 23.5
    assert result.comparedMarketValue == 20.5
    assert result.savings == -3
    assert result.savingsPercentage == -14.63
    assert result.wantsCoveredCopies == result.wantsRequestedCopies == 3
    assert result.wantsCompletionPercentage == 100
    assert result.wantsCompletedCards == 2
    assert result.wantsPurchaseCost == 14
    assert result.restPurchaseCost == 13.5
    assert result.wantsValueMinusTotalCost == -14.5
    assert result.cards[0].referenceKind == 'exact'
    assert result.cards[1].referenceKind == 'approximate'
    assert result.cards[-1].savings is None
    groups = {g.key: g for g in result.groups}
    assert groups['wants'].quantity == 3
    assert groups['decks'].quantity == 3
    assert groups['owned'].quantity == 1
    assert groups['unrelated'].quantity == 2
    assert sum(g.purchaseCost for g in result.groups) == result.totalPurchaseCost
    assert groups['wants'].savings == -1


def test_missing_price_exact_edition_foil_and_unfulfilled_wants():
    cart = CartImport(entries=[CartEntry('A', 2, None, '1', 'Example'),
                               CartEntry('B', 1, Decimal('0'), is_foil=True),
                               CartEntry('B', 1, Decimal('2'))])
    result = compare_purchase(cart, [printing('A', None), printing('A', 10, set_name='Other'),
                                    printing('B', 1, foil=5)],
                              [NS(cardName='A', quantity=3), NS(cardName='B', quantity=1)], {}, set())
    assert result.cards[0].marketUnitPrice is None  # Exact edition cannot borrow another edition's quote.
    assert result.cards[1].marketUnitPrice == 5
    assert result.cards[1].savings == 5  # Free is known, not missing.
    assert result.missingPurchasePriceCopies == 2
    assert result.missingMarketPriceCopies == 2
    assert result.wantsCompletionPercentage == 75
    assert result.wantsValueMinusTotalCost is None
    assert next(g for g in result.groups if g.key == 'extra_wants').quantity == 1


def test_parser_whitespace_amounts_and_malformed_rows():
    cart = parse_cardmarket_cart('2x\tÉowyn, Fearless Knight (V.2)\n#430\nNM\n1.234,56 €\n1\n3x Missing Price\n#2\nNM\n0x Bad\n#3\n1,00 €')
    assert [(e.name, e.quantity, e.unit_price) for e in cart.entries] == [
        ('Éowyn, Fearless Knight', 2, Decimal('1234.56')), ('Missing Price', 3, None)]
    assert len(cart.warnings) == 2
    assert parse_cardmarket_cart('1 Sol Ring\n4x Lightning Bolt (M11) 146') is None
    # Browser clipboard with literal tabs, instead of their HTML representation.
    raw = (FIXTURES / 'list-1.txt').read_text().replace('&#x9;', '\t')
    assert len(parse_cardmarket_cart(raw).entries) == 44


async def test_saved_cart_preserves_source_and_recalculates_quotes():
    raw = (FIXTURES / 'list-3.txt').read_text()
    p = printing('Hero of Precinct One', 1, '11', 'Ravnica Allegiance')
    db = database([p])
    saved = NS(id='sim', userId='user-a', name='Carrito', description=None, rawText=raw, cards=[],
               createdAt=datetime.now(timezone.utc), updatedAt=datetime.now(timezone.utc))
    db.simulatedcollection.create.return_value = saved
    db.simulatedcollection.find_unique.return_value = saved
    db.simulatedcollection.find_many.return_value = [saved]
    with patch('src.services.simulated_collection_service.db', db):
        created = await SimulatedCollectionService.create_simulated_collection('user-a', 'Carrito', None, raw)
        db.simulatedcollection.create.assert_awaited_once_with(data={'userId': 'user-a', 'name': 'Carrito', 'description': None, 'rawText': raw})
        assert created.purchaseAnalysis.cards[0].marketUnitPrice == 1
        assert created.cards[0].acquiredAt is not None
        assert db.simulatedcard.create.call_args.kwargs['data']['acquiredAt'] is not None
        p.priceCardmarketTrend = 2
        loaded = await SimulatedCollectionService.get_simulated_collection('user-a', 'sim')
        summary = await SimulatedCollectionService.list_simulated_collections('user-a')
        assert loaded.rawText == raw
        assert loaded.purchaseAnalysis.totalPurchaseCost == 3.45
        assert loaded.purchaseAnalysis.cards[0].marketUnitPrice == 2
        assert summary[0].purchaseAnalysis == loaded.purchaseAnalysis
        assert await SimulatedCollectionService.get_simulated_collection('other-user', 'sim') is None


async def test_legacy_saved_collection_has_no_invented_purchase_price():
    db = database()
    db.simulatedcollection.find_unique.return_value = NS(id='old', userId='a', name='Legacy', description=None, rawText=None,
        cards=[NS(cardName='Sol Ring', quantity=1, setCode=None, collectorNumber=None, price=7)])
    with patch('src.services.simulated_collection_service.db', db):
        result = await SimulatedCollectionService.get_simulated_collection('a', 'old')
    assert result.purchaseAnalysis is None
    assert result.totalCards == 1
    db.wantcard.find_many.assert_awaited_once_with(where={"userId": "a"})


async def test_cart_keeps_existing_deck_collection_and_surplus_metrics():
    raw = '2x Sol Ring\n#1\nNM\n0,50 €\n1x Forest\n#2\nNM\n0,10 €'
    db = database([printing('Sol Ring', 1), printing('Forest', .2)])
    db.deck.find_many.return_value = [NS(id='deck', name='Deck', cards=[
        NS(deckId='deck', cardName='Sol Ring', quantity=1, assignedQuantity=0,
           typeLine='Artifact', manaCost='{1}', imageUri=None, cardScryfallId='sol')])]
    with patch('src.services.simulated_collection_service.db', db):
        cart = await SimulatedCollectionService.analyze_raw_text('a', raw)
        plain = await SimulatedCollectionService.analyze_raw_text('a', '2 Sol Ring\n1 Forest')
    for field in ('totalEconomicValue', 'economicValueExcludingOwned', 'sellableValue',
                  'sellableCardsCount', 'globalNetGain', 'totalCards', 'uniqueCards',
                  'usefulCardsCount', 'alreadyOwnedCardsCount', 'benefitedDecksCount'):
        assert getattr(cart, field) == getattr(plain, field), field
    assert cart.usefulCardsCount == 1
    assert cart.sellableCardsCount == 2
    assert cart.globalNetGain == 100
    assert cart.purchaseAnalysis.totalPurchaseCost == 1.1


def test_price_does_not_include_numeric_seller_comment_on_previous_line():
    parsed = parse_cardmarket_cart('1x Expensive Card\n#123\nNM\n0042\n123,45 €')
    assert parsed.entries[0].unit_price == Decimal('123.45')


def test_identifies_cardmarket_commander_set_slug_with_canonical_punctuation():
    cart = CartImport(entries=[CartEntry('Shadowheart, Dark Justiciar', 1, Decimal('.90'), '146',
                                        'Commander-Legends-Battle-for-Baldurs-Gate')])
    result = compare_purchase(cart, [printing('Shadowheart, Dark Justiciar', 2, '146',
                                             "Commander Legends: Battle for Baldur's Gate")], [], {}, set())
    assert result.cards[0].referenceKind == 'exact'


async def test_plain_simulation_marks_wants_even_without_purchase_prices():
    db = database(wants=[NS(cardName="Sol Ring", quantity=1)])
    with patch('src.services.simulated_collection_service.db', db):
        result = await SimulatedCollectionService.analyze_raw_text('a', '1 Sol Ring\n1 Forest')
    assert [card.inWants for card in result.cards] == [True, False]
    assert result.purchaseAnalysis is None
    db.wantcard.find_many.assert_awaited_once_with(where={'userId': 'a'})


async def test_selected_printing_recalculates_market_without_changing_purchase_cost():
    raw = '2x Sol Ring\n#1\nNM\n0,50 €'
    cheap = printing('Sol Ring', 1)
    chosen = printing('Sol Ring', 5, '99', 'Premium')
    db = database([cheap, chosen], [NS(cardName='Sol Ring', quantity=1)])
    with patch('src.services.simulated_collection_service.db', db):
        result = await SimulatedCollectionService.analyze_raw_text('a', raw, printing_overrides={'Sol Ring': chosen.id})
    assert result.cards[0].cardScryfallId == chosen.id
    assert result.cards[0].collectorNumber == '99'
    assert result.totalEconomicValue == 10
    assert result.sellableValue == 10
    assert result.printingOverrides == {'sol ring': chosen.id}
    assert result.purchaseAnalysis.totalPurchaseCost == 1
    assert result.purchaseAnalysis.comparedMarketValue == 10
    assert result.purchaseAnalysis.savings == 9
    assert result.purchaseAnalysis.wantsCoveredCopies == 1
    assert result.purchaseAnalysis.cards[0].referenceKind == 'exact'


async def test_selected_unpriced_printing_does_not_fall_back_to_cheapest():
    chosen = printing('Sol Ring', None, '99', 'No quote')
    db = database([printing('Sol Ring', 1), chosen])
    with patch('src.services.simulated_collection_service.db', db):
        result = await SimulatedCollectionService.analyze_raw_text('a', '1x Sol Ring\n#1\nNM\n2,00 €', printing_overrides={'Sol Ring': chosen.id})
    assert result.totalEconomicValue == 0
    assert result.cards[0].cardScryfallId == chosen.id
    assert result.purchaseAnalysis.savings is None
    assert result.purchaseAnalysis.missingMarketPriceCopies == 1


async def test_version_update_persists_and_survives_reload_for_owner_only():
    from fastapi import HTTPException
    chosen = printing('Sol Ring', 5, '99', 'Premium')
    db = database([printing('Sol Ring', 1), chosen])
    card = NS(cardName='Sol Ring', quantity=2, setCode=None, collectorNumber=None, selectedPrintingId=None)
    coll = NS(id='sim', userId='a', name='Cart', description=None, rawText='2x Sol Ring\n#1\nNM\n0,50 €', cards=[card])
    db.simulatedcollection.find_unique.return_value = coll
    async def update(**kwargs):
        card.selectedPrintingId = kwargs['data']['selectedPrintingId']
        return 1
    db.simulatedcard.update_many = AsyncMock(side_effect=update)
    with patch('src.services.simulated_collection_service.db', db):
        with pytest.raises(HTTPException) as error:
            await SimulatedCollectionService.update_card_version('b', 'sim', 'Sol Ring', chosen.id)
        assert error.value.status_code == 404
        db.simulatedcard.update_many.assert_not_awaited()
        updated = await SimulatedCollectionService.update_card_version('a', 'sim', 'Sol Ring', chosen.id)
        loaded = await SimulatedCollectionService.get_simulated_collection('a', 'sim')
    assert updated.model_dump() == loaded.model_dump()
    assert loaded.cards[0].unitPrice == 5
    assert loaded.purchaseAnalysis.totalPurchaseCost == 1
    db.simulatedcard.update_many.assert_awaited_once_with(where={'simulatedCollectionId': 'sim', 'cardName': {'in': ['Sol Ring']}}, data={'selectedPrintingId': chosen.id})


@pytest.mark.parametrize('name,printing_id,code', [('Missing card', 'Sol Ring-Example-1', 404), ('Sol Ring', 'Forest-Example-1', 422)])
async def test_invalid_version_is_rejected_before_writing(name, printing_id, code):
    from fastapi import HTTPException
    db = database([printing('Sol Ring', 1), printing('Forest', 2)])
    db.simulatedcollection.find_unique.return_value = NS(id='sim', userId='a', name='Cart', description=None, rawText='1 Sol Ring', cards=[NS(cardName='Sol Ring', selectedPrintingId=None)])
    db.simulatedcard.update_many = AsyncMock()
    with patch('src.services.simulated_collection_service.db', db):
        with pytest.raises(HTTPException) as error:
            await SimulatedCollectionService.update_card_version('a', 'sim', name, printing_id)
    assert error.value.status_code == code
    db.simulatedcard.update_many.assert_not_awaited()


async def test_create_persists_temporary_version_selection():
    chosen = printing('Sol Ring', 5, '99', 'Premium')
    db = database([chosen])
    db.simulatedcollection.create.return_value = NS(id='saved')
    with patch('src.services.simulated_collection_service.db', db):
        result = await SimulatedCollectionService.create_simulated_collection('a', 'Cart', None, '1 Sol Ring', printing_overrides={'Sol Ring': chosen.id})
    assert db.simulatedcard.create.call_args.kwargs['data']['selectedPrintingId'] == chosen.id
    assert result.cards[0].unitPrice == 5


async def test_add_and_remove_individual_cards_updates_saved_simulation():
    db = database([printing('Sol Ring', 1), printing('Forest', 0.5)])
    coll = NS(id='sim', userId='a', name='Decklist', description=None,
              rawText='1 Sol Ring', cards=[NS(cardName='Sol Ring', quantity=1, selectedPrintingId=None)])
    db.simulatedcollection.find_unique.return_value = coll

    async def save_text(**kwargs):
        coll.rawText = kwargs['data']['rawText']
        return coll

    db.simulatedcollection.update.side_effect = save_text
    with patch('src.services.simulated_collection_service.db', db):
        added = await SimulatedCollectionService.mutate_card('a', 'sim', 'Forest', 'cardmarket', 'add', 2)
        coll.cards = [NS(cardName=card.cardName, quantity=card.quantity, selectedPrintingId=None) for card in added.cards]
        removed = await SimulatedCollectionService.mutate_card('a', 'sim', 'Sol Ring', 'cardmarket', 'remove')

    assert added.totalCards == 3
    assert {card.cardName for card in added.cards} == {'Sol Ring', 'Forest'}
    assert removed.totalCards == 2
    assert [card.cardName for card in removed.cards] == ['Forest']
    assert 'Sol Ring' not in coll.rawText
    assert db.simulatedcard.delete_many.await_count == 2
