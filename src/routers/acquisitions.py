from fastapi import APIRouter, Depends, Query
from src.core.auth import get_current_user_id
from src.schemas.acquisitions import (ListValidationInput,HistoricalQuoteInput,OfferQuoteInput,
                                      InventoryQuoteInput,CommitAcquisitionInput,EditAcquisitionInput)
from src.services import acquisition_service as service

router = APIRouter(prefix='/acquisitions',tags=['acquisitions'])


@router.post('/validate-list')
async def validate_list(payload: ListValidationInput,user_id: str=Depends(get_current_user_id)):
    return await service.validate_list(user_id,payload.rawText)


@router.post('/historical/quote')
async def historical_quote(payload: HistoricalQuoteInput,user_id: str=Depends(get_current_user_id)):
    return await service.create_historical_quote(user_id,payload)


@router.post('/inventory/quote')
async def inventory_quote(payload: InventoryQuoteInput,user_id: str=Depends(get_current_user_id)):
    return await service.create_inventory_quote(user_id,payload)


@router.post('/offers/{offer_id}/quote')
async def offer_quote(offer_id: str,payload: OfferQuoteInput,user_id: str=Depends(get_current_user_id)):
    return await service.create_offer_quote(user_id,offer_id,payload)


@router.post('/confirm')
async def confirm(payload: CommitAcquisitionInput,user_id: str=Depends(get_current_user_id)):
    return await service.commit_quote(user_id,payload)


@router.get('/expenses')
async def expenses(year: int|None=Query(None,ge=1900,le=2200),user_id: str=Depends(get_current_user_id)):
    return await service.ledger(user_id,year)


@router.get('/provenance/{collection_card_id}')
async def provenance(collection_card_id: str,user_id: str=Depends(get_current_user_id)):
    return await service.provenance(user_id,collection_card_id)


@router.get('/{batch_id}')
async def purchase(batch_id: str,user_id: str=Depends(get_current_user_id)):
    return await service.purchase_detail(user_id,batch_id)


@router.patch('/{batch_id}')
async def edit(batch_id: str,payload: EditAcquisitionInput,user_id: str=Depends(get_current_user_id)):
    return await service.edit_purchase(user_id,batch_id,payload)


@router.post('/{batch_id}/annul')
async def annul(batch_id: str,user_id: str=Depends(get_current_user_id)):
    return await service.annul_purchase(user_id,batch_id)
