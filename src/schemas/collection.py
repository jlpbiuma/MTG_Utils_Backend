from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from src.schemas.deck import DeckRequirement
from src.schemas.pricing import PriceSummary

class CollectionCardCreate(BaseModel):
    cardScryfallId: str
    cardName: str
    isFoil: bool = False
    quantity: int = Field(default=1, ge=1)
    setCode: Optional[str] = None
    collectorNumber: Optional[str] = None
    manaCost: Optional[str] = None
    typeLine: Optional[str] = None
    imageUri: Optional[str] = None

class CollectionCardUpdate(BaseModel):
    quantity: int = Field(..., ge=0)
    setCode: Optional[str] = None


class CollectionCardUpdateVersion(BaseModel):
    cardScryfallId: str
    imageUri: Optional[str] = None
    setCode: Optional[str] = None
    collectorNumber: Optional[str] = None


class CollectionCardResponse(BaseModel):
    id: str
    userId: str
    cardScryfallId: str
    cardName: str
    isFoil: bool = False
    quantity: int
    setCode: Optional[str] = None
    collectorNumber: Optional[str] = None
    manaCost: Optional[str] = None
    typeLine: Optional[str] = None
    imageUri: Optional[str] = None
    updatedAt: datetime
    requestedInDecks: List[DeckRequirement] = []
    requestedInDecksCount: int = 0

class CollectionStats(BaseModel):
    totalCards: int
    uniqueCards: int
    completionPercentage: float
    decksCount: Optional[int] = 0

class CollectionGroupSection(BaseModel):
    """A MTG card-type section with aggregated stats, as computed on the backend."""
    key: str
    label: str
    order: int
    totalCards: int
    uniqueCards: int
    ownedCards: int
    missingCards: int
    completionPercentage: float
    sectionTotalPrice: float
    sectionMissingPrice: float
    sectionOwnedPrice: float
    currencySymbol: str
    cards: List[CollectionCardResponse] = []

class CollectionQueryResponse(BaseModel):
    """Full collection query result: filtered, sorted, grouped on the whole collection."""
    query: str = ""
    grouped: bool = True
    provider: str = "cardmarket"
    currencySymbol: str = "€"
    totalCards: int = 0
    uniqueCards: int = 0
    page: int = 1
    limit: Optional[int] = None
    hasMore: bool = False
    sections: List[CollectionGroupSection] = []
    cards: List[CollectionCardResponse] = []


class DormantCardItem(BaseModel):
    id: str
    cardScryfallId: str
    cardName: str
    isFoil: bool = False
    quantity: int
    setCode: Optional[str] = None
    collectorNumber: Optional[str] = None
    manaCost: Optional[str] = None
    typeLine: Optional[str] = None
    imageUri: Optional[str] = None
    price: float = 0.0
    totalValue: float = 0.0


class DormantCardsResponse(BaseModel):
    totalCards: int
    uniqueCards: int
    totalValue: float
    currencySymbol: str
    provider: str
    activeCommanders: List[str] = []
    cards: List[DormantCardItem] = []


class CollectionViewResponse(BaseModel):
    """Local collection rows and cached unit prices needed by the native screen."""
    cards: List[CollectionCardResponse]
    priceSummary: PriceSummary
