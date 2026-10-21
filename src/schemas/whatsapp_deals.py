from pydantic import BaseModel
from typing import List, Optional, Dict, Any, Literal
from pydantic import Field


class ManualOpportunityRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    subject: Literal["vende", "compra_busca"]


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


class OpportunityDeckImpact(BaseModel):
    deck_card_id: str
    deck_id: str
    total_cards: int
    owned_cards: int
    quantity: int
    assigned_quantity: int
    collection_quantity: int



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
    unit_price: Optional[float] = None
    price_currency: str = "EUR"
    price_source: Optional[Literal["link", "cardmarket"]] = None
    trade_quantity: int = 0
    trend_unit_price: Optional[float] = None
    completion_gain: float = 0.0
    global_completion_after: Optional[float] = None
    completion_requirements: List[OpportunityDeckImpact] = Field(default_factory=list)


class WhatsAppMatchesResponse(BaseModel):
    wants_matches: List[WhatsAppDealMatch] = []
    collection_matches: List[WhatsAppDealMatch] = []
    total_wants_matches: int = 0
    total_collection_matches: int = 0
    total_cards_scanned: int = 0
    last_scraped_at: Optional[str] = None
    global_deck_count: int = 0
    global_completion_before: float = 0.0
