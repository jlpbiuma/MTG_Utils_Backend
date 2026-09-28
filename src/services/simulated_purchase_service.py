"""EUR purchase comparison. Unknown prices never become zero-valued losses."""
from collections import defaultdict
from decimal import Decimal
import re
import unicodedata
from typing import Any
from src.services.card_utils import normalize_card_name
from src.services.cardmarket_cart_parser import CartImport
from src.schemas.simulated_collections import PurchaseAnalysis, PurchaseCard, PurchaseGroup


def money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01")))


def _set_key(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode().lower()
    # Cardmarket moves "Commander" to the front and groups variants under Extras.
    words = re.findall(r"[a-z0-9]+", value.replace("'", ""))
    return " ".join(sorted(w for w in words if w != "extras"))


def _price(printing: Any, foil: bool) -> Decimal | None:
    fields = ("priceEurFoil",) if foil else ("priceCardmarketTrend", "priceEur")
    for field in fields:
        value = getattr(printing, field, None)
        if isinstance(value, (float, int, Decimal)) and value > 0:
            return Decimal(str(value))
    return None


def compare_purchase(cart: CartImport, printings: list[Any], wants: list[Any],
                     owned: dict[str, int], deck_names: set[str], printing_overrides: dict[str, str] | None = None) -> PurchaseAnalysis:
    by_name: dict[str, list[Any]] = defaultdict(list)
    for printing in printings:
        catalog = getattr(printing, "catalog", None)
        if catalog and isinstance(catalog.normalizedName, str):
            by_name[catalog.normalizedName].append(printing)
    wanted: dict[str, int] = defaultdict(int)
    for want in wants:
        wanted[normalize_card_name(want.cardName)] += want.quantity
    remaining = dict(wanted)
    groups = {key: PurchaseGroup(key=key) for key in ("wants", "decks", "owned", "extra_wants", "unrelated")}
    # Accumulate in decimal rather than repeatedly rounding weighted unit prices.
    sums = {key: defaultdict(Decimal) for key in groups}
    rows = []
    approximate = 0
    for entry in cart.entries:
        norm = normalize_card_name(entry.name)
        candidates = by_name.get(norm, [])
        exact = [p for p in candidates if entry.set_name and entry.collector_number
                 and str(getattr(p, "collectorNumber", "")) == entry.collector_number
                 and _set_key(getattr(getattr(p, "set", None), "name", "")) == _set_key(entry.set_name)]
        if norm in (printing_overrides or {}):
            exact = [p for p in candidates if p.id == printing_overrides[norm]]
            candidates = exact  # Explicit selections never fall back to a different edition.
        # Never substitute a cheaper edition when the identified edition has no quote.
        pool = exact if exact else candidates
        priced = [(p, _price(p, entry.is_foil)) for p in pool]
        priced = [(p, price) for p, price in priced if price is not None]
        printing, market = min(priced, key=lambda pair: pair[1]) if priced else (None, None)
        reference = "exact" if market is not None and exact else "approximate" if market is not None else "unavailable"
        if reference == "approximate":
            approximate += entry.quantity
        covered = min(entry.quantity, remaining.get(norm, 0))
        remaining[norm] = remaining.get(norm, 0) - covered
        rest_key = "decks" if norm in deck_names else "owned" if owned.get(norm, 0) else "extra_wants" if norm in wanted else "unrelated"
        for key, quantity in (("wants", covered), (rest_key, entry.quantity - covered)):
            if not quantity:
                continue
            group = groups[key]
            group.quantity += quantity
            if entry.unit_price is None:
                group.missingPurchasePriceCopies += quantity
            else:
                sums[key]["purchaseCost"] += entry.unit_price * quantity
            if market is None:
                group.missingMarketPriceCopies += quantity
            else:
                sums[key]["marketValue"] += market * quantity
            if market is not None and entry.unit_price is not None:
                group.comparedCopies += quantity
                sums[key]["comparedCost"] += entry.unit_price * quantity
                sums[key]["comparedMarketValue"] += market * quantity
        rows.append(PurchaseCard(
            cardName=entry.name, quantity=entry.quantity, collectorNumber=entry.collector_number,
            setName=entry.set_name, sourceUrl=entry.source_url, condition=entry.condition, isFoil=entry.is_foil,
            purchaseUnitPrice=float(entry.unit_price) if entry.unit_price is not None else None,
            purchaseTotal=money(entry.unit_price * entry.quantity) if entry.unit_price is not None else None,
            marketUnitPrice=float(market) if market is not None else None,
            marketTotal=money(market * entry.quantity) if market is not None else None,
            savings=money((market - entry.unit_price) * entry.quantity) if market is not None and entry.unit_price is not None else None,
            referenceKind=reference,
            referenceSet=getattr(getattr(printing, "set", None), "name", None) if printing else None,
            referenceCollectorNumber=getattr(printing, "collectorNumber", None) if printing else None,
            inWants=norm in wanted, inDecks=norm in deck_names, copiesOwned=owned.get(norm, 0), wantsCoveredCopies=covered,
        ))
    for key, group in groups.items():
        for field in ("purchaseCost", "marketValue", "comparedCost", "comparedMarketValue"):
            setattr(group, field, money(sums[key][field]))
        if group.comparedCopies:
            group.savings = money(sums[key]["comparedMarketValue"] - sums[key]["comparedCost"])
    total = lambda field: sum((sums[key][field] for key in groups), Decimal(0))
    missing_purchase = sum(g.missingPurchasePriceCopies for g in groups.values())
    compared = sum(g.comparedCopies for g in groups.values())
    savings = total("comparedMarketValue") - total("comparedCost")
    covered = sum(row.wantsCoveredCopies for row in rows)
    requested = sum(wanted.values())
    wants_group = groups["wants"]
    return PurchaseAnalysis(
        totalPurchaseCost=money(total("purchaseCost")), totalMarketValue=money(total("marketValue")),
        comparedPurchaseCost=money(total("comparedCost")), comparedMarketValue=money(total("comparedMarketValue")),
        savings=money(savings) if compared else None,
        savingsPercentage=round(float(savings / total("comparedMarketValue") * 100), 2) if total("comparedMarketValue") else None,
        comparedCopies=compared, missingPurchasePriceCopies=missing_purchase,
        missingMarketPriceCopies=sum(g.missingMarketPriceCopies for g in groups.values()),
        approximatePriceCopies=approximate,
        wantsRequestedCopies=requested, wantsCoveredCopies=covered,
        wantsCompletionPercentage=round(covered / requested * 100, 2) if requested else 0,
        wantsRequestedCards=len(wanted), wantsCompletedCards=sum(1 for norm in wanted if remaining[norm] == 0),
        wantsPurchaseCost=wants_group.purchaseCost, restPurchaseCost=money(total("purchaseCost") - sums["wants"]["purchaseCost"]),
        wantsValueMinusTotalCost=money(sums["wants"]["marketValue"] - total("purchaseCost")) if not missing_purchase and not wants_group.missingMarketPriceCopies else None,
        warnings=cart.warnings, groups=list(groups.values()), cards=rows,
    )
