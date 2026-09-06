from src.services.card_utils import normalize_card_name, get_card_category

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
