from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from src.routers import purchases
from src.schemas.purchases import ConfirmOffer, OfferUpdate
from src.schemas.simulated_collections import SimulatedCollectionAnalysisResponse, SimulatedCardAnalysisItem, PurchaseAnalysis, PurchaseCard
from src.services.offer_card_selection import remaining_offer_text
from src.services.cardmarket_cart_parser import parse_cardmarket_cart


def test_selection_retains_cardmarket_rows_prices_and_physical_quantities():
    raw = (Path(__file__).parent / 'fixtures/cardmarket/order-clipboard.txt').read_text()
    remaining = remaining_offer_text(raw, {'knight of new alara', 'kyler, sigardian emissary'})
    cart = parse_cardmarket_cart(remaining)
    assert len(cart.entries) == 22
    assert sum(card.unit_price * card.quantity for card in cart.entries) == Decimal('21.32')
    assert cart.entries[-1].name == 'The Legend of Kyoshi // Avatar Kyoshi'


def test_selection_removes_all_printings_of_a_name_in_markdown():
    raw = '| | 2x | **Sol Ring** | #1 | NM | 0,50 € |\n| | 1x | **Sol Ring** | #2 | EX | 1,00 € |\n| | 7x | **Arcane Signet** | #3 | NM | 0,10 € |'
    remaining = remaining_offer_text(raw, {'sol ring'})
    cart = parse_cardmarket_cart(remaining)
    assert [(card.name, card.quantity, card.unit_price) for card in cart.entries] == [('Arcane Signet', 7, Decimal('0.10'))]


def test_selection_preserves_legacy_sections_and_only_removes_selected_names():
    assert remaining_offer_text('7 Sol Ring\n// Mainboard\n2 Arcane Signet\n1 Sol Ring', {'sol ring'}) == '// Mainboard\n2 Arcane Signet'


@pytest.mark.asyncio
async def test_offer_update_casts_empty_source_url_to_text_for_postgres():
    database = NS(query_raw=AsyncMock(return_value=[{'id': 'offer'}]))
    payload = OfferUpdate(origin='cardmarket', seller=None, shipping=0, sourceUrl=None, paidTotal=10)
    with patch.object(purchases, 'db', database):
        result = await purchases.update_offer('offer', payload, 'owner')
    query = database.query_raw.await_args.args[0]
    assert '$6::text IS NULL' in query and 'ELSE $6::text' in query
    assert database.query_raw.await_args.args[6] is None
    assert result['id'] == 'offer'


@pytest.mark.asyncio
async def test_cardmarket_acquisition_uses_imported_price_and_selected_print_when_cart_match_is_missing():
    from src.services import acquisition_service
    from src.schemas.acquisitions import OfferQuoteInput
    card=SimulatedCardAnalysisItem(cardName='The Seriema',quantity=1,selectedPrintingId='star-trek-print',unitPrice=1.25,totalPrice=1.25)
    purchase=PurchaseCard(cardName='The Seriema',quantity=1,purchaseUnitPrice=1.25,referencePrintingId=None)
    analysis=SimulatedCollectionAnalysisResponse(id='offer',name='StarTrekV3',rawText='1 The Seriema',origin='cardmarket',shipping=0,
        cards=[card],purchaseAnalysis=PurchaseAnalysis(cards=[purchase]),status='evaluating')
    database=NS(query_raw=AsyncMock(side_effect=[
        [{'n':0,'base':'0','fee':'0'}],
        [{'id':'star-trek-print','code':'stc','collector_number':'17','image_uri':None,'mana_cost':None,'type_line':'Creature','price':2,'foil_price':None,'observed_at':None}],
    ]))
    save=AsyncMock(side_effect=lambda user,mode,data:data)
    with patch.object(acquisition_service,'db',database), \
         patch('src.services.simulated_collection_service.SimulatedCollectionService.get_simulated_collection',AsyncMock(return_value=analysis)), \
         patch.object(acquisition_service,'save_quote',save):
        quote=await acquisition_service.create_offer_quote('owner','offer',OfferQuoteInput(acquiredOn=date(2026,10,21)))
    assert quote['items'][0]['printingId']=='star-trek-print'
    assert quote['items'][0]['purchaseUnitPrice']=='1.25'
    assert quote['subtotal']=='1.25'


@pytest.mark.asyncio
@pytest.mark.parametrize('selection',[None,['Alpha']])
async def test_legacy_confirmation_uses_review_service_and_passes_budget_acknowledgement(selection):
    from src.services import acquisition_service
    database=NS(query_raw=AsyncMock(return_value=[{'status':'evaluating','confirmed_batch_id':None}]))
    prepare=AsyncMock(return_value={'quoteId':'review'})
    commit=AsyncMock(return_value={'batchId':'lot','alreadyConfirmed':False})
    with patch.object(purchases,'db',database),patch.object(acquisition_service,'create_offer_quote',prepare),patch.object(acquisition_service,'commit_quote',commit):
        result=await purchases.confirm_offer('offer',ConfirmOffer(acquiredOn=date(2026,10,3),paidTotal=999,cardNames=selection,acknowledgeBudget=True),'owner')
    assert result['batchId']=='lot'
    assert prepare.await_args.args[:2]==('owner','offer')
    assert prepare.await_args.args[2].cardNames==selection
    assert commit.await_args.args[1].quoteId=='review'
    assert commit.await_args.args[1].acknowledgeBudget


@pytest.mark.asyncio
async def test_legacy_confirm_replay_never_generates_another_quote():
    from src.services import acquisition_service
    database=NS(query_raw=AsyncMock(return_value=[{'status':'purchased','confirmed_batch_id':'existing'}]))
    prepare=AsyncMock()
    with patch.object(purchases,'db',database),patch.object(acquisition_service,'create_offer_quote',prepare):
        result=await purchases.confirm_offer('offer',ConfirmOffer(acquiredOn=date(2026,10,3),paidTotal=999),'owner')
    assert result=={'batchId':'existing','alreadyConfirmed':True}
    prepare.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_confirm_rejects_a_quote_for_another_offer():
    from src.services import acquisition_service
    database=NS(query_raw=AsyncMock(side_effect=[[{'status':'evaluating','confirmed_batch_id':None}],[{'payload':{'offerId':'other'}}]]))
    commit=AsyncMock()
    with patch.object(purchases,'db',database),patch.object(acquisition_service,'commit_quote',commit):
        with pytest.raises(HTTPException) as error:
            await purchases.confirm_offer('offer',ConfirmOffer(quoteId='foreign',acquiredOn=date(2026,10,3),paidTotal=1),'owner')
    assert error.value.status_code==404
    commit.assert_not_awaited()
