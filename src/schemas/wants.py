from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from src.schemas.deck import DeckRequirement


class WantCardCreate(BaseModel):
    cardScryfallId: str
    cardName: str
    quantity: int = Field(default=1, ge=1)
    setCode: Optional[str] = None
    collectorNumber: Optional[str] = None
    manaCost: Optional[str] = None
    typeLine: Optional[str] = None
    imageUri: Optional[str] = None


class WantCardUpdate(BaseModel):
    quantity: int = Field(..., ge=0)
    setCode: Optional[str] = None


class WantCardUpdateVersion(BaseModel):
    cardScryfallId: str
    imageUri: Optional[str] = None
    setCode: Optional[str] = None
    collectorNumber: Optional[str] = None


class WantCardResponse(BaseModel):
    id: str
    userId: str
    cardScryfallId: str
    cardName: str
    quantity: int
    setCode: Optional[str] = None
    collectorNumber: Optional[str] = None
    manaCost: Optional[str] = None
    typeLine: Optional[str] = None
    imageUri: Optional[str] = None
    updatedAt: datetime
    requestedInDecks: List[DeckRequirement] = []
    requestedInDecksCount: int = 0


class WantStats(BaseModel):
    totalCards: int
    uniqueCards: int


class WantGroupSection(BaseModel):
    key: str
    label: str
    order: int
    totalCards: int
    uniqueCards: int
    ownedCards: int = 0
    missingCards: int = 0
    completionPercentage: float = 0.0
    sectionTotalPrice: float = 0.0
    sectionMissingPrice: float = 0.0
    sectionOwnedPrice: float = 0.0
    currencySymbol: str = "€"
    cards: List[WantCardResponse] = []


class WantQueryResponse(BaseModel):
    query: str = ""
    grouped: bool = True
    provider: str = "cardmarket"
    currencySymbol: str = "€"
    totalCards: int = 0
    uniqueCards: int = 0
    sections: List[WantGroupSection] = []
    cards: List[WantCardResponse] = []
