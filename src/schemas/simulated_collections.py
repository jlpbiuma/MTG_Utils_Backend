from src.schemas.price_statistics import PriceHistoryStatistics
from src.schemas.whatsapp_deals import WhatsAppMatchesResponse
from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from datetime import datetime

class PurchaseGroup(BaseModel):
    key: str
    quantity: int = 0
    purchaseCost: float = 0.0
    marketValue: float = 0.0
    comparedCost: float = 0.0
    comparedMarketValue: float = 0.0
    savings: Optional[float] = None
    comparedCopies: int = 0
    missingPurchasePriceCopies: int = 0
    missingMarketPriceCopies: int = 0


class PurchaseCard(BaseModel):
    historyPrintingId: Optional[str] = None
    historyStatistics: Optional[PriceHistoryStatistics] = None
    referencePrintingId: Optional[str] = None
    referenceSetCode: Optional[str] = None
    cardName: str
    quantity: int
    collectorNumber: Optional[str] = None
    setName: Optional[str] = None
    sourceUrl: Optional[str] = None
    condition: Optional[str] = None
    isFoil: bool = False
    purchaseUnitPrice: Optional[float] = None
    purchaseTotal: Optional[float] = None
    marketUnitPrice: Optional[float] = None
    marketTotal: Optional[float] = None
    savings: Optional[float] = None
    referenceKind: Literal["exact", "approximate", "unavailable"] = "unavailable"
    referenceSet: Optional[str] = None
    referenceCollectorNumber: Optional[str] = None
    inWants: bool = False
    inDecks: bool = False
    copiesOwned: int = 0
    wantsCoveredCopies: int = 0


class PurchaseAnalysis(BaseModel):
    historyStatistics: Optional[PriceHistoryStatistics] = None
    historyCoveredCopies: int = 0
    missingHistoryCopies: int = 0
    currency: str = "EUR"
    totalPurchaseCost: float = 0.0
    totalMarketValue: float = 0.0
    comparedPurchaseCost: float = 0.0
    comparedMarketValue: float = 0.0
    savings: Optional[float] = None
    savingsPercentage: Optional[float] = None
    comparedCopies: int = 0
    missingPurchasePriceCopies: int = 0
    missingMarketPriceCopies: int = 0
    approximatePriceCopies: int = 0
    wantsRequestedCopies: int = 0
    wantsCoveredCopies: int = 0
    wantsCompletionPercentage: float = 0.0
    wantsRequestedCards: int = 0
    wantsCompletedCards: int = 0
    wantsPurchaseCost: float = 0.0
    restPurchaseCost: float = 0.0
    wantsValueMinusTotalCost: Optional[float] = None
    warnings: List[str] = []
    groups: List[PurchaseGroup] = []
    cards: List[PurchaseCard] = []


class CandidateDeckInfo(BaseModel):
    deckId: str
    deckName: str
    completionPercentage: float
    colors: List[str] = []
    requestedQuantity: int
    assignedQuantity: int
    missingQuantity: int
    potentialGain: float = 0.0
    totalCards: Optional[int] = None
    ownedCards: Optional[int] = None

class UpcomingReprint(BaseModel):
    setCode: str
    setName: str
    releaseDate: str


class SimulatedCardAnalysisItem(BaseModel):
    upcomingReprints: List[UpcomingReprint] = Field(default_factory=list)
    historyStatistics: Optional[PriceHistoryStatistics] = None
    selectedPrintingId: Optional[str] = None
    inWants: bool = False
    cardName: str
    sourceNames: List[str] = []
    cardScryfallId: Optional[str] = None
    quantity: int
    acquiredAt: Optional[datetime] = None
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
    origin: Literal["whatsapp", "cardmarket", "in_person", "other"] = "other"
    opportunitySubject: Literal["vende", "compra_busca"] = "vende"
    globalCompletionBefore: float = 0
    globalCompletionAfter: float = 0
    opportunityMatches: Optional[WhatsAppMatchesResponse] = None
    status: Literal["evaluating", "purchased", "discarded"] = "evaluating"
    seller: Optional[str] = None
    shipping: float = 0
    sourceUrl: Optional[str] = None
    paidTotal: Optional[float] = None
    confirmedBatchId: Optional[str] = None

    purchaseAnalysis: Optional[PurchaseAnalysis] = None
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
    valuationRevision: Optional[str] = None
    origin: Literal["whatsapp", "cardmarket", "in_person", "other"] = "other"
    opportunitySubject: Literal["vende", "compra_busca"] = "vende"
    globalCompletionBefore: float = 0
    globalCompletionAfter: float = 0
    opportunityMatches: Optional[WhatsAppMatchesResponse] = None
    historyStatistics: Optional[PriceHistoryStatistics] = None
    historyCoveredCopies: int = 0
    missingHistoryCopies: int = 0
    status: Literal["evaluating", "purchased", "discarded"] = "evaluating"
    seller: Optional[str] = None
    shipping: float = 0
    sourceUrl: Optional[str] = None
    paidTotal: Optional[float] = None
    confirmedBatchId: Optional[str] = None

    printingOverrides: dict[str, str] = Field(default_factory=dict)
    rawText: Optional[str] = None
    purchaseAnalysis: Optional[PurchaseAnalysis] = None
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
    origin: Literal["whatsapp", "cardmarket", "in_person", "other"] = "other"
    printingOverrides: dict[str, str] = Field(default_factory=dict)
    name: str
    description: Optional[str] = None
    rawText: str

class SimulatedCollectionAnalyzeRequest(BaseModel):
    includeHistory: bool = True
    printingOverrides: dict[str, str] = Field(default_factory=dict)
    rawText: str
    provider: str = "cardmarket"


class SimulatedCardVersionRequest(BaseModel):
    cardName: str = Field(min_length=1)
    printingId: str = Field(min_length=1)


class SimulatedCollectionCardRequest(BaseModel):
    cardName: str = Field(min_length=1)
    quantity: int = Field(default=1, ge=1, le=999)


class OfferLinkImportRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    name: Optional[str] = None
    origin: Literal["whatsapp", "cardmarket", "in_person", "other"] = "other"
    subject: Literal["vende", "compra_busca"] = "vende"
    savedOnly: bool = False
