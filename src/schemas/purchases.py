from datetime import date
from decimal import Decimal
from typing import Literal, Annotated
from pydantic import BaseModel, Field, HttpUrl

Money = Decimal

from src.schemas.acquisitions import DatedInput

class BatchInput(DatedInput):
    setCode: str | None = Field(default=None, max_length=20)
    kind: Literal['prerelease', 'booster_box', 'bundle', 'singles', 'trade', 'other', 'precon', 'mini_collection', 'collection'] = 'singles'
    acquiredOn: date
    costTotal: Money = Field(ge=0, max_digits=12, decimal_places=2)
    shipping: Money = Field(default=Decimal('0'), ge=0, max_digits=12, decimal_places=2)
    currency: Literal['EUR', 'USD'] = 'EUR'
    acknowledgeBudget: bool = False
    notes: str | None = Field(default=None, max_length=4000)

class Assignment(BaseModel):
    collectionCardId: str
    quantity: int = Field(ge=1, le=99999)

class AssignInput(BaseModel):
    remove: list[str] = Field(default_factory=list, max_length=500)
    items: list[Assignment] = Field(max_length=500)

class OfferUpdate(BaseModel):
    origin: Literal["whatsapp", "cardmarket", "in_person", "other"] | None = None
    status: Literal['evaluating', 'discarded'] = 'evaluating'
    seller: str | None = Field(default=None, max_length=200)
    shipping: Money = Field(default=Decimal('0'), ge=0, max_digits=12, decimal_places=2)
    sourceUrl: HttpUrl | None = None
    paidTotal: Money | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)

class ConfirmOffer(BaseModel):
    quoteId: str | None = None
    acknowledgeBudget: bool = False
    cardNames: list[str] | None = Field(default=None, min_length=1, max_length=500)
    acquiredOn: date
    paidTotal: Money = Field(ge=0, max_digits=12, decimal_places=2)

class Preferences(BaseModel):
    priceProvider: Literal['cardmarket', 'cardtrader', 'mtggoldfish'] = 'cardmarket'

class FunctionTargets(BaseModel):
    targets: dict[Literal['ramp','draw','removal','wipes','tutors','protection'], Annotated[int, Field(ge=0, le=100)]]
