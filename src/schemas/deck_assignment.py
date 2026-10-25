from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class DeckAssignmentNoticeResponse(BaseModel):
    id: str
    deckId: str
    cardName: str
    deckName: str
    quantity: int
    createdAt: datetime
    imageUri: Optional[str] = None
