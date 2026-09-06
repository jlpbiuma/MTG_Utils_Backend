from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime

class DeckCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    format: str = Field(default="Commander")
    description: Optional[str] = None
    commander: Optional[str] = None
    commanderScryfallId: Optional[str] = None
    commanderImageUri: Optional[str] = None

class DeckUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    format: Optional[str] = None
    description: Optional[str] = None
    commander: Optional[str] = None
    commanderScryfallId: Optional[str] = None
    commanderImageUri: Optional[str] = None

class SetCommanderRequest(BaseModel):
    commander: str
    commanderScryfallId: Optional[str] = None
    commanderImageUri: Optional[str] = None

class DeckCardCreate(BaseModel):
    cardScryfallId: str
    cardName: str
    quantity: int = Field(default=1, ge=1)
    isSideboard: bool = False
    isCommander: bool = False
    manaCost: Optional[str] = None
    typeLine: Optional[str] = None
    imageUri: Optional[str] = None

class DeckCardUpdateQuantity(BaseModel):
    quantity: int = Field(..., ge=1)

class DeckCardAssign(BaseModel):
    quantity: int = Field(default=1, ge=1)

class DeckCardRelease(BaseModel):
    quantity: int = Field(default=1, ge=1)

class DeckCardReassign(BaseModel):
    sourceDeckId: str
    quantity: int = Field(default=1, ge=1)

class OtherDeckAssignment(BaseModel):
    deckId: str
    deckName: str
    quantity: int

class DeckCardWithOwnership(BaseModel):
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
    ownedInCollection: int
    availableToAssign: int
    assignedInOtherDecks: List[OtherDeckAssignment] = []
    missingCount: int

class DeckSummaryResponse(BaseModel):
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

class DeckDetailResponse(BaseModel):
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
    cards: List[DeckCardWithOwnership] = []
