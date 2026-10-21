"""Published base checklists; extra product printings remain browsable.

MSC #1–290: Wizards MSH_Cardlist_06122026.pdf, Commander section.
Do not infer base ranges for other sets from catalog size or card identity.
"""
BASE_CHECKLIST_LIMITS = {"msc": 290}


def is_base_printing(code: str, number: str) -> bool:
    limit = BASE_CHECKLIST_LIMITS.get(code.lower())
    return limit is None or (number.isascii() and number.isdigit() and 1 <= int(number) <= limit)


def base_printing_sql(set_alias: str = "s", printing_alias: str = "p") -> str:
    rules = " ".join(
        f"WHEN lower({set_alias}.code)='{code}' THEN "
        f"({printing_alias}.collector_number ~ '^[0-9]+$' AND "
        f"CASE WHEN {printing_alias}.collector_number ~ '^[0-9]+$' "
        f"THEN {printing_alias}.collector_number::numeric BETWEEN 1 AND {limit} ELSE false END)"
        for code, limit in BASE_CHECKLIST_LIMITS.items()
    )
    return f"CASE {rules} ELSE true END"
