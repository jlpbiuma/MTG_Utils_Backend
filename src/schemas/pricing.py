from pydantic import BaseModel, Field, model_validator
from typing import Optional, Dict, List, Literal
from datetime import datetime

PriceProvider = Literal["cardmarket", "cardtrader", "mtggoldfish"]

class UnitPriceBreakdown(BaseModel):
    trend: float = 0.0
    min: float = 0.0
    max: float = 0.0

class CardPriceQuote(BaseModel):
    scryfallId: Optional[str] = None
    cardName: str
    provider: PriceProvider = "cardmarket"
    currency: str = "EUR"
    currencySymbol: str = "€"
    unitPrice: UnitPriceBreakdown = UnitPriceBreakdown()
    quantity: int = 1
    subtotal: float = 0.0
    purchaseUrl: Optional[str] = None
    lastUpdated: datetime

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_id(cls, values):
        if isinstance(values, dict) and "scryfallId" not in values:
            values = dict(values)
            values["scryfallId"] = values.pop("cardScryfallId", None)
        return values

    # Compatibility accessors for older backend callers/tests.
    @property
    def cardScryfallId(self) -> Optional[str]: return self.scryfallId
    @property
    def trendPrice(self) -> float: return self.unitPrice.trend
    @property
    def minPrice(self) -> float: return self.unitPrice.min
    @property
    def maxPrice(self) -> float: return self.unitPrice.max
    @property
    def productUrl(self) -> Optional[str]: return self.purchaseUrl

class PriceSummary(BaseModel):
    provider: PriceProvider
    currency: str
    currencySymbol: str
    totalCards: int = 0
    totalNetValue: float
    totalMissingValue: float
    totalOwnedValue: float
    quotes: Dict[str, CardPriceQuote] = {}
    lastUpdated: datetime

    # Compatibility accessors for old API consumers.
    @property
    def totalValue(self) -> float: return self.totalNetValue
    @property
    def missingCardsValue(self) -> float: return self.totalMissingValue
    @property
    def ownedCardsValue(self) -> float: return self.totalOwnedValue
    @property
    def cards(self) -> Dict[str, CardPriceQuote]: return self.quotes

class PricingRequest(BaseModel):
    provider: PriceProvider = Field(default="cardmarket")
    deckId: Optional[str] = None
    includeCollection: bool = False
    forceRefresh: bool = False

class PricingCard(BaseModel):
    name: str = Field(..., min_length=1)
    scryfallId: Optional[str] = None
    quantity: int = Field(default=1, ge=1)
    isMissing: bool = False
    ownedQuantity: Optional[int] = None
    missingQuantity: Optional[int] = None

class PricingCardsRequest(BaseModel):
    cards: List[PricingCard] = Field(..., min_length=1)
    provider: PriceProvider = Field(default="cardmarket")
    forceRefresh: bool = False

class PriceHistoryPoint(BaseModel):
    provider: PriceProvider
    currency: str
    trendPrice: Optional[float] = None
    minPrice: Optional[float] = None
    maxPrice: Optional[float] = None
    recordedAt: datetime

class CardPrintingResponse(BaseModel):
    id: str
    catalogId: Optional[str] = None
    setCode: str
    collectorNumber: str
    rarity: Optional[str] = None
    imageUri: Optional[str] = None
    imageUriSmall: Optional[str] = None
    imageUriLarge: Optional[str] = None
    priceCardmarketTrend: Optional[float] = None
    priceCardmarketMin: Optional[float] = None
    priceCardmarketMax: Optional[float] = None
    priceCardtraderTrend: Optional[float] = None
    priceCardtraderMin: Optional[float] = None
    priceCardtraderMax: Optional[float] = None

class CardSetResponse(BaseModel):
    code: str
    name: str
    setType: str
    cardCount: int
    releasedAt: Optional[datetime] = None
