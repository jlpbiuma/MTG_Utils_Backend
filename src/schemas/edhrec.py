from pydantic import BaseModel
from typing import Optional, List
from src.schemas.deck import DeckRequirement, DeckCardWithOwnership

class EdhrecCardRecommendation(BaseModel):
    id: str
    name: str
    normalizedName: str
    sanitized: str
    category: str
    categories: List[str] = []
    numDecks: int
    potentialDecks: int
    inclusionPct: float
    synergy: float
    imageUri: Optional[str] = None
    isInDeck: bool = False
    isInCollection: bool = False
    collectionQuantity: int = 0
    requestedInDecks: List[DeckRequirement] = []
    requestedInDecksCount: int = 0
    isInWant: bool = False

class CommanderStats(BaseModel):
    name: str
    imageUri: Optional[str] = None
    numDecks: int = 0
    colorIdentity: List[str] = []

class DeckRecommendationsResponse(BaseModel):
    error: Optional[str] = None
    commander: Optional[CommanderStats] = None
    categories: List[str] = []
    recommendations: List[EdhrecCardRecommendation] = []


class CommanderTypeBreakdown(BaseModel):
    creatures: int = 0
    instants: int = 0
    sorceries: int = 0
    artifacts: int = 0
    enchantments: int = 0
    battle: int = 0
    planeswalkers: int = 0
    lands: int = 0
    basicLands: int = 0
    nonbasicLands: int = 0


class CommanderTypeOwnership(BaseModel):
    creaturesOwned: int = 0
    creaturesTotal: int = 0
    instantsOwned: int = 0
    instantsTotal: int = 0
    sorceriesOwned: int = 0
    sorceriesTotal: int = 0
    artifactsOwned: int = 0
    artifactsTotal: int = 0
    enchantmentsOwned: int = 0
    enchantmentsTotal: int = 0
    planeswalkersOwned: int = 0
    planeswalkersTotal: int = 0
    nonbasicLandsOwned: int = 0
    nonbasicLandsTotal: int = 0


class RecommendationCoverage(BaseModel):
    owned: int = 0
    total: int = 0
    percentage: Optional[float] = None


class CommanderRecommendationSummary(BaseModel):
    id: str
    name: str
    normalizedName: str
    slug: str
    imageUri: Optional[str] = None
    colorIdentity: List[str] = []
    isTop100: bool = False
    edhrecRank: Optional[int] = None
    numDecks: int = 0
    typeBreakdown: CommanderTypeBreakdown
    typeOwnership: CommanderTypeOwnership
    completionPercentage: float = 0.0
    ownedCardsCount: int = 0
    totalRequiredCards: int = 0
    userOwnsCommander: bool = False
    ownedValue: float = 0.0
    missingValue: float = 0.0
    unpricedCards: int = 0
    unfilledSlots: int = 0
    highSynergyCoverage: RecommendationCoverage = RecommendationCoverage()
    topCardsCoverage: RecommendationCoverage = RecommendationCoverage()


class CommanderRecommendationsListResponse(BaseModel):
    total: int
    page: int
    pageSize: int
    totalPages: int
    commanders: List[CommanderRecommendationSummary]


from src.schemas.deck import DeckDetailResponse
from src.schemas.pricing import PriceSummary


class RecommendedDeckCard(DeckCardWithOwnership):
    isInWant: bool = False
    inclusionPct: float = 0.0
    synergy: float = 0.0
    isTopCard: bool = False
    isHighSynergy: bool = False
    edhrecCategory: str = "other"


class RecommendedDeckResponse(DeckDetailResponse):
    cards: List[RecommendedDeckCard] = []
    typeQuotas: dict[str, int] = {}
    basicLandQuota: int = 0
    priceSummary: PriceSummary
    unpricedCards: int = 0
    unfilledSlots: int = 0
    colors: List[str] = []
