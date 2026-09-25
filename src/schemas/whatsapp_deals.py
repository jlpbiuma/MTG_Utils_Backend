from pydantic import BaseModel
from typing import List, Optional, Dict, Any


class CatalogCardSummary(BaseModel):
    card_id: Optional[str] = None
    name: str
    image_uri: Optional[str] = None
    type_line: Optional[str] = None
    mana_cost: Optional[str] = None
    cmc: Optional[float] = None
    rarity: Optional[str] = None
    price_cardmarket: Optional[float] = None


class WantMatchInfo(BaseModel):
    want_id: str
    quantity_wanted: int
    requested_decks: List[str] = []


class CollectionMatchInfo(BaseModel):
    collection_id: str
    quantity_owned: int
    available_quantity: int = 0


class WhatsAppDealMatch(BaseModel):
    id: int
    card_name: str
    normalized_name: str
    set_code: Optional[str] = None
    price: Optional[str] = None
    currency: Optional[str] = None
    condition: Optional[str] = None
    subject: str  # "vende" | "compra_busca"
    whatsapp_url: str
    source_phone: str
    source_chat: Optional[str] = None
    raw_message: Optional[str] = None
    detected_at: Optional[str] = None
    direct_whatsapp_link: str
    extra: Dict[str, Any] = {}
    catalog_card: Optional[CatalogCardSummary] = None
    matched_want: Optional[WantMatchInfo] = None
    matched_collection: Optional[CollectionMatchInfo] = None


class WhatsAppMatchesResponse(BaseModel):
    wants_matches: List[WhatsAppDealMatch] = []
    collection_matches: List[WhatsAppDealMatch] = []
    total_wants_matches: int = 0
    total_collection_matches: int = 0
    total_cards_scanned: int = 0
    last_scraped_at: Optional[str] = None
