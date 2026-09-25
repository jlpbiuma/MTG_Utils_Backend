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
    deckTotalCards: Optional[int] = None
    deckMissingCards: Optional[int] = None

class PriorityPricePoint(BaseModel):
    date: str
    price: float


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
    priceHistory: List[PriorityPricePoint] = []
    historicalLow: Optional[float] = None
    atHistoricalLow: bool = False
    change30dPercent: Optional[float] = None
    priceChangePercent: Optional[float] = None
    isReassignable: bool
    reassignOptions: List[DeckReassignOption] = []
    maxDeckCompletion: float = 0.0
    maxPotentialGain: float = 0.0
    avgPotentialGain: float = 0.0
    netCompletionGain: float = 0.0
    sumPointsGain: float = 0.0

class PrioritiesResponse(BaseModel):
    priceWindowDays: int = 30
    globalDeckCount: int = 0
    globalCompletionBefore: float = 0.0
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

class GoldenWantsTargetDeck(BaseModel):
    deckId: str
    deckName: str
    completionBefore: float

class GoldenWantsCartItem(BaseModel):
    cardName: str
    cardScryfallId: str
    imageUri: Optional[str] = None
    manaCost: Optional[str] = None
    typeLine: Optional[str] = None
    quantityToBuy: int
    unitPrice: float
    totalCost: float
    targetDecks: List[GoldenWantsTargetDeck] = []

class CompletedDeckInfo(BaseModel):
    deckId: str
    deckName: str

class ProjectedDeckProgress(BaseModel):
    deckId: str
    deckName: str
    before: float
    after: float
    gainedPercentage: float
    cardsFulfilled: int
    totalMissingInitially: int

class GoldenWantsResponse(BaseModel):
    cart: List[GoldenWantsCartItem] = []
    totalCost: float = 0.0
    budgetRemaining: float = 0.0
    completedDecks: List[CompletedDeckInfo] = []
    projectedProgress: List[ProjectedDeckProgress] = []
    totalCardsToBuy: int = 0
