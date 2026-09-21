from pydantic import BaseModel, Field
from typing import Optional, List

class ParsedCardEntry(BaseModel):
    name: str
    quantity: int = 1
    setCode: Optional[str] = None
    collectorNumber: Optional[str] = None
    isFoil: bool = False
    isSideboard: bool = False
    isCommander: bool = False

class ParsedDecklist(BaseModel):
    mainboard: List[ParsedCardEntry] = []
    sideboard: List[ParsedCardEntry] = []
    totalCards: int = 0

class ParseTextRequest(BaseModel):
    text: str

class ImportDeckTextRequest(BaseModel):
    name: str = Field(..., min_length=1)
    text: str
    format: str = "Commander"
    commander: Optional[str] = None

class ImportMoxfieldRequest(BaseModel):
    url: str
    format: Optional[str] = "Commander"

class ImportCollectionTextRequest(BaseModel):
    text: str = Field(..., max_length=2_000_000)
    requestKey: Optional[str] = Field(default=None, min_length=1, max_length=128)
