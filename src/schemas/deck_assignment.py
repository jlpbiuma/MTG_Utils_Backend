from datetime import datetime
from pydantic import BaseModel


class DeckAssignmentNoticeResponse(BaseModel):
    id: str
    deckId: str
    cardName: str
    deckName: str
    quantity: int
    createdAt: datetime
