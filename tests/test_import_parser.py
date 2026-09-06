from src.services.import_service import parse_decklist_text, parse_decklist

def test_parse_plain_text():
    raw = """
    4 Lightning Bolt
    1 Sol Ring
    2x Counterspell
    """
    cards = parse_decklist_text(raw)
    assert len(cards) == 3
    assert cards[0].name == "Lightning Bolt"
    assert cards[0].quantity == 4
    assert not cards[0].isSideboard
    assert cards[1].name == "Sol Ring"
    assert cards[1].quantity == 1
    assert cards[2].name == "Counterspell"
    assert cards[2].quantity == 2

def test_parse_moxfield_and_sideboard():
    raw = """
    // Commander
    1 Niv-Mizzet, Parun (GRN) 192 *F*

    // Main
    1 Sol Ring (C21) 263
    1 Arcane Signet (C20) 237

    // Sideboard
    1 Red Elemental Blast (A25) 147
    SB: 1 Dispel
    """
    result = parse_decklist(raw)
    assert len(result.mainboard) == 3  # Niv-Mizzet (Commander) + Sol Ring + Arcane Signet
    assert len(result.sideboard) == 2
    assert result.totalCards == 5

    cmd = result.mainboard[0]
    assert cmd.name == "Niv-Mizzet, Parun"
    assert cmd.isCommander is True
    assert cmd.setCode == "grn"
    assert cmd.collectorNumber == "192"
    assert cmd.isFoil is True

    sb1 = result.sideboard[0]
    assert sb1.name == "Red Elemental Blast"
    assert sb1.isSideboard is True

    sb2 = result.sideboard[1]
    assert sb2.name == "Dispel"
    assert sb2.isSideboard is True
