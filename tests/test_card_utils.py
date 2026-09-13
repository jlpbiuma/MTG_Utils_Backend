from src.services.card_utils import (
    normalize_card_name,
    get_card_category,
    is_basic_land,
    extract_colors_from_mana_cost,
    combine_colors_from_mana_costs,
)

def test_normalize_card_name():
    assert normalize_card_name("Sol Ring") == "sol ring"
    assert normalize_card_name("  Lightning Bolt  ") == "lightning bolt"
    assert normalize_card_name("Fire // Ice") == "fire"
    assert normalize_card_name("Wear // Tear") == "wear"
    assert normalize_card_name("") == ""
    assert normalize_card_name(None) == ""

def test_get_card_category_standard_types():
    assert get_card_category("Creature — Human Wizard") == "creatures"
    assert get_card_category("Artifact Creature — Golem") == "creatures" # Creature takes precedence
    assert get_card_category("Legendary Planeswalker — Jace") == "planeswalkers"
    assert get_card_category("Instant") == "instants"
    assert get_card_category("Sorcery") == "sorceries"
    assert get_card_category("Artifact — Equipment") == "artifacts"
    assert get_card_category("Enchantment — Aura") == "enchantments"
    assert get_card_category("Battle — Siege") == "battles"
    assert get_card_category("Basic Land — Island") == "lands"
    assert get_card_category("Token") == "other"

def test_get_card_category_spanish_types():
    assert get_card_category("Criatura legendaria — Dragón") == "creatures"
    assert get_card_category("Instantáneo") == "instants"
    assert get_card_category("Conjuro") == "sorceries"
    assert get_card_category("Artefacto") == "artifacts"
    assert get_card_category("Encantamiento") == "enchantments"
    assert get_card_category("Tierra") == "lands"

def test_get_card_category_fallback_heuristics():
    # Without type_line, basic lands are detected by name
    assert get_card_category(None, "Island") == "lands"
    assert get_card_category(None, "Mountain") == "lands"
    assert get_card_category(None, "Forest") == "lands"
    assert get_card_category(None, "Plains") == "lands"
    assert get_card_category(None, "Swamp") == "lands"
    assert get_card_category(None, "Snow-Covered Island") == "lands"
    assert get_card_category(None, "Command Tower") == "lands"
    assert get_card_category(None, "Reliquary Tower") == "lands"
    assert get_card_category(None, "Evolving Wilds") == "lands"
    assert get_card_category(None, "Unknown Card") == "other"


def test_is_basic_land_by_type_line():
    assert is_basic_land("Basic Land — Island") is True
    assert is_basic_land("Basic Land — Snow-Covered Forest") is True
    assert is_basic_land("Basic Land — Wastes") is True
    assert is_basic_land("Land — Island") is False
    assert is_basic_land("Creature — Merfolk") is False
    assert is_basic_land(None) is False


def test_is_basic_land_by_name():
    for name in ["Plains", "Island", "Swamp", "Mountain", "Forest",
                 "Wastes", "Snow-Covered Plains", "Snow-Covered Island",
                 "Snow-Covered Swamp", "Snow-Covered Mountain", "Snow-Covered Forest"]:
        assert is_basic_land(None, name) is True
        assert is_basic_land("", name) is True
    assert is_basic_land(None, "Command Tower") is False
    assert is_basic_land(None, "Evolving Wilds") is False
    assert is_basic_land(None, "Sol Ring") is False


def test_extract_colors_from_mana_cost():
    assert extract_colors_from_mana_cost(None) == []
    assert extract_colors_from_mana_cost("") == []
    assert extract_colors_from_mana_cost("{1}") == []
    assert extract_colors_from_mana_cost("{X}{G}") == ["G"]
    assert extract_colors_from_mana_cost("{2}{W}{U}") == ["W", "U"]
    # Canonical WUBRG ordering regardless of appearance order.
    assert extract_colors_from_mana_cost("{G}{R}{B}{U}{W}") == ["W", "U", "B", "R", "G"]
    # Hybrid / Phyrexian symbols contribute every contained color.
    assert extract_colors_from_mana_cost("{W/U}") == ["W", "U"]
    assert extract_colors_from_mana_cost("{2/W}") == ["W"]
    assert extract_colors_from_mana_cost("{W/P}") == ["W"]
    assert extract_colors_from_mana_cost("{U/R}{B}") == ["U", "B", "R"]


def test_combine_colors_from_mana_costs():
    assert combine_colors_from_mana_costs(["{W}{U}", "{B}"]) == ["W", "U", "B"]
    assert combine_colors_from_mana_costs(["{G}", None, "{R}"]) == ["R", "G"]
    assert combine_colors_from_mana_costs([]) == []
