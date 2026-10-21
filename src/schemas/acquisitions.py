from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field, field_validator

Origin = Literal['cardmarket', 'whatsapp', 'in_person', 'other']
Kind = Literal['precon', 'mini_collection', 'collection', 'singles', 'other', 'prerelease', 'booster_box', 'bundle', 'trade']


def local_today():
    return datetime.now(ZoneInfo('Atlantic/Canary')).date()


class DatedInput(BaseModel):
    acquiredOn: date

    @field_validator('name', mode='before', check_fields=False)
    @classmethod
    def trimmed_name(cls, value):
        return value.strip() if isinstance(value,str) else value

    @field_validator('acquiredOn')
    @classmethod
    def not_future(cls, value):
        if value > local_today():
            raise ValueError('La fecha de adquisición no puede ser posterior a hoy')
        return value


class ListValidationInput(BaseModel):
    rawText: str = Field(min_length=1, max_length=40000)


class HistoricalSelection(BaseModel):
    line: int = Field(ge=1)
    collectionCardId: str
    quantity: int = Field(ge=1, le=99999)


class HistoricalQuoteInput(DatedInput):
    rawText: str = Field(min_length=1, max_length=40000)
    total: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    name: str = Field(min_length=1, max_length=200)
    kind: Kind = 'mini_collection'
    origin: Origin = 'other'
    seller: str | None = Field(default=None, max_length=200)
    sourceUrl: str | None = Field(default=None, max_length=2000)
    selections: list[HistoricalSelection] = Field(min_length=1, max_length=500)


class OfferQuoteInput(DatedInput):
    valuationRevision: str | None = None
    cardNames: list[str] | None = Field(default=None, min_length=1, max_length=500)
    purchasePrices: dict[str, Decimal] = Field(default_factory=dict)

    @field_validator('purchasePrices')
    @classmethod
    def valid_prices(cls, prices):
        if any(not p.is_finite() or p < 0 or p != p.quantize(Decimal('0.01')) or p > Decimal('9999999999') for p in prices.values()):
            raise ValueError('Los precios deben ser importes positivos con dos decimales')
        return prices


class InventoryQuoteInput(DatedInput):
    printingId: str
    quantity: int = Field(ge=1, le=99999)
    isFoil: bool = False
    name: str = Field(default='Compra de cartas', min_length=1, max_length=200)


class CommitAcquisitionInput(BaseModel):
    quoteId: str
    acknowledgeBudget: bool = False


class EditAcquisitionInput(DatedInput):
    name: str = Field(min_length=1, max_length=200)
    total: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    seller: str | None = Field(default=None, max_length=200)
    acknowledgeBudget: bool = False

    shipping: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    purchasePrices: dict[str, Decimal] = Field(default_factory=dict)

    _validate_prices = field_validator('purchasePrices')(OfferQuoteInput.valid_prices.__func__)
