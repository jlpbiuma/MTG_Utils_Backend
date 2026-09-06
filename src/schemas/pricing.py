from pydantic import BaseModel, Field
from typing import Optional, Dict
from datetime import datetime

class CardPriceQuote(BaseModel):
    cardScryfallId: str
    cardName: str
    trendPrice: Optional[float] = None
    minPrice: Optional[float] = None
    maxPrice: Optional[float] = None
    currency: str = "EUR"
    productUrl: Optional[str] = None

class PriceSummary(BaseModel):
    provider: str
    currency: str
    currencySymbol: str
    totalValue: float
    missingCardsValue: float
    ownedCardsValue: float
    cards: Dict[str, CardPriceQuote] = {}
    lastUpdated: datetime

class PricingRequest(BaseModel):
    provider: str = Field(default="cardmarket")
    deckId: Optional[str] = None
    includeCollection: bool = False
    forceRefresh: bool = False
