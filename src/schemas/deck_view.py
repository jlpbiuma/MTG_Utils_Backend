"""Read-only contract for the native deck screen; no catalog/editor payloads."""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel
from src.schemas.deck import OtherDeckAssignment
from src.schemas.pricing import PriceSummary


class DeckViewCard(BaseModel):
    id: str
    deckId: str
    cardScryfallId: str
    cardName: str
    quantity: int
    assignedQuantity: int
    isSideboard: bool
    isCommander: bool
    manaCost: Optional[str] = None
    typeLine: Optional[str] = None
    imageUri: Optional[str] = None
    setCode: Optional[str] = None
    ownedInCollection: int
    availableToAssign: int
    assignedInOtherDecks: list[OtherDeckAssignment]
    missingCount: int


class DeckViewResponse(BaseModel):
    id: str
    userId: str
    name: str
    format: str
    description: Optional[str] = None
    commander: Optional[str] = None
    commanderScryfallId: Optional[str] = None
    commanderImageUri: Optional[str] = None
    createdAt: datetime
    updatedAt: datetime
    totalCards: int
    uniqueCards: int
    ownedCards: int
    missingCards: int
    completionPercentage: float
    cards: list[DeckViewCard]
    priceSummary: PriceSummary
    commanderColorIdentity: list[str]

