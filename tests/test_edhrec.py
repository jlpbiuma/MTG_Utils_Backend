from src.services.edhrec_service import to_edhrec_slug, get_edhrec_card_image_url

def test_to_edhrec_slug():
    assert to_edhrec_slug("Atris, Oracle of Half-Truths") == "atris-oracle-of-half-truths"
    assert to_edhrec_slug("Niv-Mizzet, Parun") == "niv-mizzet-parun"
    assert to_edhrec_slug("Kethis, the Hidden Hand") == "kethis-the-hidden-hand"
    assert to_edhrec_slug("Urza, Lord High Artificer") == "urza-lord-high-artificer"
    assert to_edhrec_slug("Fire // Ice") == "fire-ice"
    assert to_edhrec_slug("Jace, the Mind Sculptor") == "jace-the-mind-sculptor"
    assert to_edhrec_slug("") == ""

def test_get_edhrec_card_image_url():
    scryfall_id = "44445555-6666-7777-8888-99990000aaaa"
    url = get_edhrec_card_image_url(scryfall_id)
    assert url == "https://card-images.edhrec.com/normal/front/4/4/44445555-6666-7777-8888-99990000aaaa.jpg"

    assert get_edhrec_card_image_url("") is None
    assert get_edhrec_card_image_url("a") is None
