from pydantic import BaseModel, Field
from typing import List, Optional


class CardScanCatalog(BaseModel):
    id: str
    name: str
    normalizedName: str
    manaCost: Optional[str] = None
    typeLine: Optional[str] = None
    imageUri: Optional[str] = None


class CardScanPrinting(BaseModel):
    id: str
    catalogId: Optional[str] = None
    setCode: Optional[str] = None
    collectorNumber: Optional[str] = None
    imageUri: Optional[str] = None
    priceEur: Optional[float] = None
    priceCardmarketTrend: Optional[float] = None


class CardScanAlternative(BaseModel):
    id: str
    name: str
    matchScore: int
    setCode: Optional[str] = None
    collectorNumber: Optional[str] = None
    imageUri: Optional[str] = None


class CardScanResponse(BaseModel):
    ocrTitle: str
    matchScore: int
    catalog: CardScanCatalog
    printing: Optional[CardScanPrinting] = None
    alternatives: List[CardScanAlternative] = Field(default_factory=list)
