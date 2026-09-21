from pydantic import BaseModel
from typing import Optional, List
from src.schemas.deck import DeckRequirement

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
