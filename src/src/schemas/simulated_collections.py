from pydantic import BaseModel
from typing import Optional, List

class CandidateDeckInfo(BaseModel):
    deckId: str
    deckName: str
    completionPercentage: float
    colors: List[str] = []
    requestedQuantity: int
    assignedQuantity: int
    missingQuantity: int
    potentialGain: float = 0.0

class SimulatedCardAnalysisItem(BaseModel):
    cardName: str
    cardScryfallId: Optional[str] = None
    quantity: int
    setCode: Optional[str] = None
    collectorNumber: Optional[str] = None
    manaCost: Optional[str] = None
    typeLine: Optional[str] = None
    imageUri: Optional[str] = None
    unitPrice: float = 0.0
    totalPrice: float = 0.0
    copiesOwnedReal: int = 0
    copiesNeededTotal: int = 0
    usefulCopies: int = 0
    surplusCopies: int = 0
    sellableCopies: int = 0
    sellableValue: float = 0.0
    netCompletionGain: float = 0.0
    candidateDeckCount: int = 0
    candidateDecks: List[CandidateDeckInfo] = []

class SimulatedCollectionSummary(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    totalCards: int = 0
    uniqueCards: int = 0
    totalEconomicValue: float = 0.0
    economicValueExcludingOwned: float = 0.0
    sellableValue: float = 0.0
    sellableCardsCount: int = 0
    globalNetGain: float = 0.0
    usefulCardsCount: int = 0
    alreadyOwnedCardsCount: int = 0
    benefitedDecksCount: int = 0
    createdAt: str
    updatedAt: str

class SimulatedCollectionAnalysisResponse(BaseModel):
    id: Optional[str] = None
    name: str
    description: Optional[str] = None
    totalEconomicValue: float = 0.0
    economicValueExcludingOwned: float = 0.0
    sellableValue: float = 0.0
    sellableCardsCount: int = 0
    currencySymbol: str = "€"
    globalNetGain: float = 0.0
    totalCards: int = 0
    uniqueCards: int = 0
    usefulCardsCount: int = 0
    alreadyOwnedCardsCount: int = 0
    benefitedDecksCount: int = 0
    cards: List[SimulatedCardAnalysisItem] = []



class SimulatedCollectionCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    rawText: str

class SimulatedCollectionAnalyzeRequest(BaseModel):
    rawText: str
    provider: str = "cardmarket"
