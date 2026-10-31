-- Case-insensitive card name lookups (assignment notices, acquisitions and
-- expansion card decks) compare LOWER(name); without these indexes each one
-- scanned the whole catalog or deck list. Read-path only, no data changes.
CREATE INDEX IF NOT EXISTS card_catalog_lower_name_idx ON card_catalog (LOWER(name));
-- statement-break
CREATE INDEX IF NOT EXISTS deck_cards_deck_lower_name_idx ON deck_cards (deck_id, LOWER(card_name));
