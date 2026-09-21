from pydantic import BaseModel
from typing import Optional, List

class DeckReassignOption(BaseModel):
    sourceDeckId: str
    sourceDeckName: str
    sourceDeckCompletion: float
    assignedQuantity: int
    targetDeckId: str
    targetDeckName: str
    targetDeckCompletion: float
    missingQuantity: int
    targetDeckCardId: str

class PriorityDeckInfo(BaseModel):
    deckId: str
    deckName: str
    completionPercentage: float
    colors: List[str] = []
    requestedQuantity: int
    assignedQuantity: int
    missingQuantity: int
    deckCardId: str
    potentialGain: float = 0.0

class PriorityItem(BaseModel):
    cardName: str
    cardScryfallId: str
    imageUri: Optional[str] = None
    manaCost: Optional[str] = None
    typeLine: Optional[str] = None
    cardType: str = "other"
    numDecks: int
    decks: List[PriorityDeckInfo] = []
    copiesOwned: int
    copiesNeeded: int
    deficit: int
    price: float
    totalDeficitCost: float
    isReassignable: bool
    reassignOptions: List[DeckReassignOption] = []
    maxDeckCompletion: float = 0.0
    maxPotentialGain: float = 0.0
    avgPotentialGain: float = 0.0
    netCompletionGain: float = 0.0
    sumPointsGain: float = 0.0

class PrioritiesResponse(BaseModel):
    totalUniqueCards: int
    totalDeficitCopies: int
    totalDeficitCost: float
    currencySymbol: str
    provider: str
    page: int = 1
    limit: int = 30
    totalItems: int = 0
    hasMore: bool = False
    items: List[PriorityItem] = []
