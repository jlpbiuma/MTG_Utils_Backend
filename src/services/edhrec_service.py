import httpx
import unicodedata
import re
import time
import logging
from typing import List, Dict, Any, Optional
from src.core.db import db
import math
from src.schemas.edhrec import (
    EdhrecCardRecommendation,
    CommanderTypeBreakdown,
    CommanderTypeOwnership,
    CommanderRecommendationSummary,
    CommanderRecommendationsListResponse,
)
from src.services.card_utils import (
    normalize_card_name,
    is_basic_land,
    completion_percentages_by_deck,
    colors_by_deck,
)

logger = logging.getLogger("mtg_backend.edhrec")

def to_edhrec_slug(name: str) -> str:
    if not name:
        return ""
    s = name.lower()
    # Remove accents
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = re.sub(r"\s*//\s*", "-", s)
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"\s+", "-", s.strip())
    s = re.sub(r"-+", "-", s)
    return s

def get_edhrec_card_image_url(card_id: str) -> Optional[str]:
    if not card_id or len(card_id) < 2:
        return None
    return f"https://card-images.edhrec.com/normal/front/{card_id[0]}/{card_id[1]}/{card_id}.jpg"

# In-memory cache with 24h TTL
_edhrec_cache: Dict[str, Dict[str, Any]] = {}
CACHE_TTL_SECONDS = 24 * 3600

class EdhrecService:
    @staticmethod
    async def fetch_edhrec_data(commander_name: str) -> Optional[Dict[str, Any]]:
        slug = to_edhrec_slug(commander_name)
        if not slug:
            return None

        now = time.time()
        if slug in _edhrec_cache:
            entry = _edhrec_cache[slug]
            if now - entry["timestamp"] < CACHE_TTL_SECONDS:
                return entry["data"]

        url = f"https://json.edhrec.com/pages/commanders/{slug}.json"
        headers = {
            "User-Agent": "MTGUtils/2.0 (FastAPI-Python-Backend)",
            "Accept": "application/json",
        }

        async with httpx.AsyncClient(headers=headers, timeout=12.0) as client:
            try:
                res = await client.get(url)
                if res.status_code != 200:
                    logger.warning(f"EDHREC returned {res.status_code} for slug '{slug}'")
                    return None
                raw = res.json()
            except Exception as e:
                logger.error(f"Error requesting EDHREC for '{commander_name}': {e}")
                return None

        # EDHREC serves card lists under container.json_dict.cardlists
        # (not a top-level "cardlist" key).
        json_dict = (raw.get("container") or {}).get("json_dict") or {}
        cardlist = json_dict.get("cardlists") or raw.get("cardlist") or []
        categories: List[str] = []
        cards_map: Dict[str, Dict[str, Any]] = {}

        for group in cardlist:
            tag = group.get("header") or group.get("tag") or "Recommendations"
            if tag not in categories:
                categories.append(tag)

            for cv in group.get("cardviews", []):
                name = cv.get("name", "")
                if not name:
                    continue

                card_id = cv.get("id") or cv.get("sanitized", "")
                num_decks = cv.get("num_decks", 0)
                potential_decks = cv.get("potential_decks", 0)
                inclusion_pct = round((num_decks / potential_decks) * 100, 1) if potential_decks > 0 else 0.0
                synergy = round(cv.get("synergy", 0.0) * 100, 1) if "synergy" in cv else 0.0
                img = get_edhrec_card_image_url(card_id)

                norm = normalize_card_name(name)
                if norm in cards_map:
                    if tag not in cards_map[norm]["categories"]:
                        cards_map[norm]["categories"].append(tag)
                    if inclusion_pct > cards_map[norm]["inclusionPct"]:
                        cards_map[norm]["inclusionPct"] = inclusion_pct
                        cards_map[norm]["numDecks"] = num_decks
                        cards_map[norm]["potentialDecks"] = potential_decks
                    if synergy > cards_map[norm]["synergy"]:
                        cards_map[norm]["synergy"] = synergy
                    if not cards_map[norm]["imageUri"] and img:
                        cards_map[norm]["imageUri"] = img
                else:
                    cards_map[norm] = {
                        "id": card_id,
                        "name": name,
                        "normalizedName": norm,
                        "sanitized": cv.get("sanitized", ""),
                        "category": tag,
                        "categories": [tag],
                        "numDecks": num_decks,
                        "potentialDecks": potential_decks,
                        "inclusionPct": inclusion_pct,
                        "synergy": synergy,
                        "imageUri": img,
                    }

        parsed_cards = list(cards_map.values())
        parsed_cards.sort(key=lambda c: c["inclusionPct"], reverse=True)

        result = {
            "categories": categories,
            "cards": parsed_cards,
        }

        _edhrec_cache[slug] = {
            "timestamp": now,
            "data": result,
        }
        return result

    @staticmethod
    async def get_deck_recommendations(deck_id: str, current_user_id: str) -> "DeckRecommendationsResponse":
        from src.schemas.edhrec import DeckRecommendationsResponse, CommanderStats

        # Fetch deck
        deck = await db.deck.find_unique(where={"id": deck_id})
        if not deck:
            return DeckRecommendationsResponse(error="Mazo no encontrado")

        commander_name = deck.commander
        if not commander_name:
            # Fallback: check deck cards with isCommander
            cmd_card = await db.deckcard.find_first(
                where={"deckId": deck_id, "isCommander": True}
            )
            if cmd_card:
                commander_name = cmd_card.cardName

        if not commander_name:
            return DeckRecommendationsResponse(error="El mazo no tiene comandante asignado")

        edhrec_data = await EdhrecService.fetch_edhrec_data(commander_name)
        if not edhrec_data:
            return DeckRecommendationsResponse(error=f"No se encontraron recomendaciones en EDHREC para {commander_name}")

        # Fetch deck cards for ownership matching
        deck_cards = await db.deckcard.find_many(where={"deckId": deck_id})
        deck_set = {normalize_card_name(c.cardName) for c in deck_cards}

        # Fetch owner collection cards (sum quantities across printings)
        collection_owner_id = deck.userId or current_user_id
        col_cards = await db.collectioncard.find_many(where={"userId": collection_owner_id})
        col_map: Dict[str, int] = {}
        for cc in col_cards:
            norm_col = normalize_card_name(cc.cardName)
            col_map[norm_col] = col_map.get(norm_col, 0) + (cc.quantity or 0)

        # Cross-deck demand by normalized name (same as deck detail view)
        from src.schemas.deck import DeckRequirement

        user_deck_cards = await db.deckcard.find_many(
            where={"deck": {"userId": collection_owner_id}},
            include={"deck": True},
        )
        decks_by_norm: Dict[str, Dict[str, DeckRequirement]] = {}
        for oc in user_deck_cards or []:
            norm = normalize_card_name(oc.cardName)
            deck_obj = getattr(oc, "deck", None)
            d_id = getattr(oc, "deckId", None) or (
                deck_obj.id if deck_obj and getattr(deck_obj, "id", None) else None
            )
            if not d_id:
                continue
            d_name = (
                deck_obj.name
                if (deck_obj and isinstance(getattr(deck_obj, "name", None), str))
                else ("Este mazo" if d_id == deck_id else "Otro Mazo")
            )
            if d_id == deck_id and getattr(deck, "name", None):
                d_name = deck.name
            qty = getattr(oc, "quantity", None) or 0
            if norm not in decks_by_norm:
                decks_by_norm[norm] = {}
            if d_id not in decks_by_norm[norm]:
                decks_by_norm[norm][d_id] = DeckRequirement(
                    deckId=d_id,
                    deckName=d_name,
                    quantity=qty,
                )
            else:
                decks_by_norm[norm][d_id].quantity += qty

        completion = completion_percentages_by_deck(user_deck_cards, col_map)
        colors = colors_by_deck(user_deck_cards)
        for by_deck in decks_by_norm.values():
            for req in by_deck.values():
                req.completionPercentage = completion.get(req.deckId, 0.0)
                req.colors = colors.get(req.deckId, [])

        # Wants list (by normalized name)
        want_cards = await db.wantcard.find_many(where={"userId": collection_owner_id})
        want_set = {normalize_card_name(w.cardName) for w in want_cards}

        recommendations: List[EdhrecCardRecommendation] = []
        for c in edhrec_data.get("cards", []):
            norm = c["normalizedName"]
            is_in_deck = norm in deck_set
            col_qty = col_map.get(norm, 0)
            is_in_col = col_qty > 0

            req_dict = decks_by_norm.get(norm, {})
            req_list = sorted(
                list(req_dict.values()),
                key=lambda r: (0 if r.deckId == deck_id else 1, r.deckName.lower()),
            )

            recommendations.append(EdhrecCardRecommendation(
                id=c["id"],
                name=c["name"],
                normalizedName=norm,
                sanitized=c["sanitized"],
                category=c["category"],
                categories=c.get("categories", [c["category"]]),
                numDecks=c["numDecks"],
                potentialDecks=c["potentialDecks"],
                inclusionPct=c["inclusionPct"],
                synergy=c["synergy"],
                imageUri=c.get("imageUri"),
                isInDeck=is_in_deck,
                isInCollection=is_in_col,
                collectionQuantity=col_qty,
                requestedInDecks=req_list,
                requestedInDecksCount=len(req_list),
                isInWant=norm in want_set,
            ))

        return DeckRecommendationsResponse(
            commander=CommanderStats(
                name=commander_name,
                imageUri=deck.commanderImageUri or get_edhrec_card_image_url(deck.commanderScryfallId or ""),
                numDecks=0,
                colorIdentity=[],
            ),
            categories=edhrec_data.get("categories", []),
            recommendations=recommendations,
        )

    @staticmethod
    async def get_commander_recommendations_for_user(
        user_id: str,
        search: Optional[str] = None,
        colors: Optional[str] = None,
        top100_only: bool = False,
        owned_commander_only: bool = False,
        sort_by: str = "completion",
        sort_dir: Optional[str] = None,
        page: int = 1,
        page_size: int = 24,
    ) -> CommanderRecommendationsListResponse:
        # Load user collection
        collection_cards = await db.collectioncard.find_many(where={"userId": user_id})
        user_collection_names = {
            normalize_card_name(c.cardName)
            for c in (collection_cards or [])
            if (c.quantity or 0) > 0 and c.cardName
        }

        # Include archived decks and every explicitly marked commander (partners too).
        decks = await db.deck.find_many(where={"userId": user_id})
        commander_cards = await db.deckcard.find_many(
            where={"deck": {"userId": user_id}, "isCommander": True}
        )
        existing_commanders = {
            normalize_card_name(d.commander) for d in decks if d.commander
        } | {
            normalize_card_name(c.cardName) for c in commander_cards if c.cardName
        }

        # Pending/failed downloads cannot provide meaningful completion values.
        where_filter: Dict[str, Any] = {"status": "synced"}
        if top100_only:
            where_filter["isTop100"] = True

        db_commanders = await db.edhreccommander.find_many(
            where=where_filter,
            order=[{"isTop100": "desc"}, {"rank": "asc"}, {"name": "asc"}],
        )

        target_colors = None
        if colors:
            target_colors = set(c.upper() for c in colors.replace(",", " ").split() if c)

        search_term = search.strip().lower() if search else None

        summaries: List[CommanderRecommendationSummary] = []
        commanders_by_slug = {}
        for cmd in db_commanders:
            cmd_norm = cmd.normalizedName or normalize_card_name(cmd.name)
            if normalize_card_name(cmd_norm) in existing_commanders:
                continue
            user_owns_cmd = cmd_norm in user_collection_names

            if owned_commander_only and not user_owns_cmd:
                continue

            if search_term and search_term not in cmd.name.lower():
                continue

            cmd_colors = [c.upper() for c in (cmd.colorIdentity or "") if c.upper() in "WUBRG"]
            if target_colors is not None:
                if not set(cmd_colors).issubset(target_colors):
                    continue

            # Type breakdown
            tb = CommanderTypeBreakdown(
                creatures=cmd.creatureCount,
                instants=cmd.instantCount,
                sorceries=cmd.sorceryCount,
                artifacts=cmd.artifactCount,
                enchantments=cmd.enchantmentCount,
                battle=cmd.battleCount,
                planeswalkers=cmd.planeswalkerCount,
                lands=cmd.landCount,
                basicLands=cmd.basicLandCount,
                nonbasicLands=cmd.nonbasicLandCount,
            )

            cards_json = cmd.cardsJson if isinstance(cmd.cardsJson, dict) else {}
            canonical_names = cmd.canonicalCardNames if isinstance(cmd.canonicalCardNames, list) else []

            from src.services.edhrec_deck_service import CATEGORIES, select_recommended_cards, group_coverage
            commanders_by_slug[cmd.slug] = cmd
            selected_cards, _ = select_recommended_cards(cmd, user_collection_names)

            def calc_type_ownership(tags: List[str], quota: int) -> tuple[int, int]:
                type_line = next(t for category_tags, _, t in CATEGORIES if category_tags == tags)
                owned = sum(
                    1 for norm, _, selected_type in selected_cards
                    if selected_type == type_line and norm in user_collection_names
                )
                return owned, max(0, quota)

            if cards_json:
                cr_owned, cr_total = calc_type_ownership(["creatures"], cmd.creatureCount)
                in_owned, in_total = calc_type_ownership(["instants"], cmd.instantCount)
                so_owned, so_total = calc_type_ownership(["sorceries"], cmd.sorceryCount)
                ar_owned, ar_total = calc_type_ownership(["utilityartifacts", "manaartifacts", "artifacts"], cmd.artifactCount)
                en_owned, en_total = calc_type_ownership(["enchantments"], cmd.enchantmentCount)
                pw_owned, pw_total = calc_type_ownership(["planeswalkers"], cmd.planeswalkerCount)
                ld_owned, ld_total = calc_type_ownership(["utilitylands", "lands"], cmd.nonbasicLandCount)

                battle_owned, battle_total = calc_type_ownership(["battles"], cmd.battleCount)

                total_owned = battle_owned + cr_owned + in_owned + so_owned + ar_owned + en_owned + pw_owned + ld_owned
                total_req = battle_total + cr_total + in_total + so_total + ar_total + en_total + pw_total + ld_total
            else:
                cr_owned = in_owned = so_owned = ar_owned = en_owned = pw_owned = ld_owned = 0
                cr_total = in_total = so_total = ar_total = en_total = pw_total = ld_total = 0
                total_req = len(canonical_names)
                total_owned = sum(1 for c in canonical_names if c in user_collection_names)

            ownership = CommanderTypeOwnership(
                creaturesOwned=cr_owned,
                creaturesTotal=cr_total,
                instantsOwned=in_owned,
                instantsTotal=in_total,
                sorceriesOwned=so_owned,
                sorceriesTotal=so_total,
                artifactsOwned=ar_owned,
                artifactsTotal=ar_total,
                enchantmentsOwned=en_owned,
                enchantmentsTotal=en_total,
                planeswalkersOwned=pw_owned,
                planeswalkersTotal=pw_total,
                nonbasicLandsOwned=ld_owned,
                nonbasicLandsTotal=ld_total,
            )

            pct = round((total_owned / total_req) * 100, 1) if total_req > 0 else 0.0

            img_uri = get_edhrec_card_image_url(cmd.id)

            summaries.append(CommanderRecommendationSummary(
                id=str(cmd.id or ""),
                name=str(cmd.name or ""),
                normalizedName=str(cmd_norm or ""),
                slug=str(cmd.slug or ""),
                imageUri=img_uri,
                colorIdentity=cmd_colors,
                isTop100=bool(cmd.isTop100),
                edhrecRank=cmd.rank,
                numDecks=int(cmd.numDecks or 0),
                typeBreakdown=tb,
                typeOwnership=ownership,
                completionPercentage=pct,
                ownedCardsCount=total_owned,
                totalRequiredCards=total_req,
                userOwnsCommander=user_owns_cmd,
                highSynergyCoverage=group_coverage(cmd, "highsynergycards", user_collection_names),
                topCardsCoverage=group_coverage(cmd, "topcards", user_collection_names),
            ))

        from src.services.edhrec_deck_service import recommendation_prices, estimate_values, with_commander_for_pricing

        if sort_by in ("owned_value", "missing_value"):
            all_selections, all_candidates = {}, {}
            for summary in summaries:
                cmd_obj = commanders_by_slug.get(summary.slug)
                if not cmd_obj:
                    continue
                selected, required = select_recommended_cards(cmd_obj, user_collection_names)
                selected = with_commander_for_pricing(cmd_obj, selected)
                all_selections[summary.slug] = (selected, required + 1)
                all_candidates.update({norm: card for norm, card, _ in selected})
            quotes = {}
            if all_candidates:
                quotes, _ = await recommendation_prices(all_candidates, collection_cards, db)
            for summary in summaries:
                if summary.slug in all_selections:
                    selected, required = all_selections[summary.slug]
                    for field, value in estimate_values(selected, required, user_collection_names, quotes).items():
                        setattr(summary, field, value)

        direction = (sort_dir or "").lower()

        if sort_by == "rank":
            is_asc = direction == "asc" if direction in ("asc", "desc") else True
            summaries.sort(
                key=lambda s: (
                    0 if s.edhrecRank else 1,
                    (s.edhrecRank or 9999) if is_asc else -(s.edhrecRank or 0),
                    -s.completionPercentage,
                )
            )
        elif sort_by == "name":
            is_asc = direction == "asc" if direction in ("asc", "desc") else True
            summaries.sort(key=lambda s: s.name.lower(), reverse=not is_asc)
        elif sort_by == "owned_value":
            is_asc = direction == "asc" if direction in ("asc", "desc") else False
            summaries.sort(
                key=lambda s: (
                    s.ownedValue if is_asc else -s.ownedValue,
                    -s.completionPercentage,
                    0 if s.edhrecRank else 1,
                    s.edhrecRank or 9999,
                )
            )
        elif sort_by == "missing_value":
            is_asc = direction == "asc" if direction in ("asc", "desc") else True
            summaries.sort(
                key=lambda s: (
                    s.missingValue if is_asc else -s.missingValue,
                    -s.completionPercentage,
                    0 if s.edhrecRank else 1,
                    s.edhrecRank or 9999,
                )
            )
        elif sort_by == "synergy":
            is_asc = direction == "asc" if direction in ("asc", "desc") else False
            summaries.sort(
                key=lambda s: (
                    (s.highSynergyCoverage.percentage if s.highSynergyCoverage and s.highSynergyCoverage.percentage is not None else (999.0 if is_asc else -1.0)) * (1 if is_asc else -1),
                    -s.completionPercentage,
                    0 if s.edhrecRank else 1,
                    s.edhrecRank or 9999,
                )
            )
        elif sort_by == "top_cards":
            is_asc = direction == "asc" if direction in ("asc", "desc") else False
            summaries.sort(
                key=lambda s: (
                    (s.topCardsCoverage.percentage if s.topCardsCoverage and s.topCardsCoverage.percentage is not None else (999.0 if is_asc else -1.0)) * (1 if is_asc else -1),
                    -s.completionPercentage,
                    0 if s.edhrecRank else 1,
                    s.edhrecRank or 9999,
                )
            )
        else: # "completion"
            is_asc = direction == "asc" if direction in ("asc", "desc") else False
            summaries.sort(
                key=lambda s: (
                    s.completionPercentage if is_asc else -s.completionPercentage,
                    0 if s.edhrecRank else 1,
                    s.edhrecRank or 9999,
                    -s.numDecks,
                )
            )

        total_items = len(summaries)
        page_size = max(1, min(page_size, 100))
        page = max(1, page)
        total_pages = math.ceil(total_items / page_size) if total_items > 0 else 1

        start_idx = (page - 1) * page_size
        paged_items = summaries[start_idx : start_idx + page_size]

        if sort_by not in ("owned_value", "missing_value"):
            selections, candidates = {}, {}
            for summary in paged_items:
                cmd_obj = commanders_by_slug.get(summary.slug)
                if not cmd_obj:
                    continue
                selected, required = select_recommended_cards(cmd_obj, user_collection_names)
                selected = with_commander_for_pricing(cmd_obj, selected)
                selections[summary.slug] = (selected, required + 1)
                candidates.update({norm: card for norm, card, _ in selected})
            quotes = {}
            if candidates:
                quotes, _ = await recommendation_prices(candidates, collection_cards, db)
            for summary in paged_items:
                if summary.slug in selections:
                    selected, required = selections[summary.slug]
                    for field, value in estimate_values(selected, required, user_collection_names, quotes).items():
                        setattr(summary, field, value)

        return CommanderRecommendationsListResponse(
            total=total_items,
            page=page,
            pageSize=page_size,
            totalPages=total_pages,
            commanders=paged_items,
        )

