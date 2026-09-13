from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

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

class CollectionStats(BaseModel):
    totalCards: int
    uniqueCards: int
    completionPercentage: float
