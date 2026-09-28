"""Parse copied Cardmarket carts without treating seller comments as cards."""
from dataclasses import dataclass, field
from decimal import Decimal
import html
import re
from typing import Optional


@dataclass
class CartEntry:
    name: str
    quantity: int
    unit_price: Optional[Decimal] = None
    collector_number: Optional[str] = None
    set_name: Optional[str] = None
    source_url: Optional[str] = None
    condition: Optional[str] = None
    is_foil: bool = False


@dataclass
class CartImport:
    entries: list[CartEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


PRODUCT_LINK = re.compile(r"\[([^\]]+)\]\((https://www\.cardmarket\.com/[^)]+/Products/Singles/([^/]+)/[^)]+)\)", re.I)
PRICE = re.compile(r"(?<![\w.,-])(\d+(?:[. ]\d{3})*,\d{2}|\d+\.\d{2}|\d+)\s*€")


def parse_cardmarket_cart(raw_text: str) -> Optional[CartImport]:
    text = html.unescape(raw_text).replace("\\#", "#").replace("\u00a0", " ").replace("\u202f", " ")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    # Markdown tables become the same stream of fields as a browser clipboard.
    text = text.replace("|", "\n")
    markers = list(re.finditer(r"(?im)^\s*(\d+)x\b[ \t]*(.*)$", text))
    if not markers or not ("€" in text or PRODUCT_LINK.search(text) or re.search(r"(?m)^\s*#\w+", text)):
        return None
    result = CartImport()
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        block = text[marker.end():end]
        fields = [s.strip() for s in (marker.group(2) + "\n" + block).splitlines() if s.strip()]
        quantity = int(marker.group(1))
        if not fields or quantity < 1:
            result.warnings.append(f"Fila {index + 1}: cantidad o nombre inválido; no se importó.")
            continue
        name_field = fields[0]
        link = PRODUCT_LINK.search(name_field)
        name = (link.group(1) if link else name_field).strip("* ")
        name = re.sub(r"\s*\(V\.\s*\d+\)\s*$", "", name, flags=re.I).strip()
        if not name or name.startswith("#") or PRICE.fullmatch(name):
            result.warnings.append(f"Fila {index + 1}: falta el nombre; no se importó.")
            continue
        metadata = "\n".join(fields[1:])
        price = PRICE.search(metadata)
        amount = None
        if price:
            value = price.group(1).replace(" ", "")
            if "," in value:
                value = value.replace(".", "").replace(",", ".")
            amount = Decimal(value)
        else:
            result.warnings.append(f"{name}: no se pudo leer el precio de compra.")
        collector = re.search(r"#([\w-]+)", metadata)
        condition = re.search(r"\b(MT|NM|EX|GD|LP|PL|PO)\b", metadata)
        result.entries.append(CartEntry(
            name=name, quantity=quantity, unit_price=amount,
            collector_number=collector.group(1) if collector else None,
            set_name=link.group(3) if link else None,
            source_url=link.group(2) if link else None,
            condition=condition.group(1) if condition else None,
            is_foil=any(f.lower() == "foil" for f in fields[1:]),
        ))
    return result
