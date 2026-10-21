CREATE TABLE deck_assignment_notices (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  deck_id TEXT NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
  card_name TEXT NOT NULL,
  deck_name TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  created_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  accepted_at TIMESTAMP(3)
);
-- statement-break
CREATE INDEX deck_assignment_notices_user_id_accepted_at_idx ON deck_assignment_notices(user_id, accepted_at);
-- statement-break
-- Mirrors normalize_card_name: whitespace, case and front face of split cards.
CREATE FUNCTION mtg_assignment_name(value TEXT) RETURNS TEXT
LANGUAGE SQL IMMUTABLE STRICT AS $$
  SELECT trim(split_part(regexp_replace(regexp_replace(lower(trim(value)), '\s+', ' ', 'g'), '\s*/+\s*', ' // ', 'g'), ' // ', 1));
$$;
-- statement-break
-- Lock before modifying inventory so simultaneous acquisitions cannot overassign.
CREATE FUNCTION mtg_lock_acquisition() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' OR NEW.quantity > OLD.quantity THEN
    PERFORM pg_advisory_xact_lock(hashtextextended('mtg-acquisition:' || NEW.user_id, 0));
  END IF;
  RETURN NEW;
END;
$$;
-- statement-break
CREATE TRIGGER lock_deck_acquisition BEFORE INSERT OR UPDATE OF quantity ON user_collections
FOR EACH ROW EXECUTE FUNCTION mtg_lock_acquisition();
-- statement-break
-- All inventory writers (imports, purchases, quantity edits and deck actions)
-- receive the same assignment and persistent notification, atomically.
CREATE FUNCTION mtg_assign_acquisition() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
  added INTEGER;
  available INTEGER;
  used INTEGER;
  target RECORD;
  normalized TEXT := mtg_assignment_name(NEW.card_name);
BEGIN
  IF current_setting('mtg.suppress_acquisition', true) = 'on' THEN RETURN NEW; END IF;
  added := NEW.quantity;
  IF TG_OP = 'UPDATE' THEN
    added := NEW.quantity - OLD.quantity;
  END IF;
  IF added <= 0 THEN RETURN NEW; END IF;
  SELECT LEAST(added, GREATEST(0,
    (SELECT COALESCE(SUM(quantity), 0) FROM user_collections
      WHERE user_id = NEW.user_id AND mtg_assignment_name(card_name) = normalized)
    - (SELECT COALESCE(SUM(c.assigned_quantity), 0) FROM deck_cards c JOIN decks d ON d.id = c.deck_id
      WHERE d.user_id = NEW.user_id AND NOT d.is_archived AND mtg_assignment_name(c.card_name) = normalized)
  )) INTO available;
  FOR target IN
    SELECT c.id, c.quantity, c.assigned_quantity, c.card_name, d.id AS deck_id, d.name AS deck_name
    FROM deck_cards c JOIN decks d ON d.id = c.deck_id
    WHERE d.user_id = NEW.user_id AND NOT d.is_archived
      AND mtg_assignment_name(c.card_name) = normalized AND c.assigned_quantity < c.quantity
    ORDER BY (d.id = COALESCE(current_setting('mtg.preferred_deck', true), '')) DESC, d.created_at, d.id, c.is_sideboard, c.id
    FOR UPDATE OF c
  LOOP
    EXIT WHEN available <= 0;
    used := LEAST(available, target.quantity - target.assigned_quantity);
    UPDATE deck_cards SET assigned_quantity = assigned_quantity + used WHERE id = target.id;
    INSERT INTO deck_assignment_notices(id, user_id, deck_id, card_name, deck_name, quantity)
      VALUES(gen_random_uuid()::text, NEW.user_id, target.deck_id, target.card_name, target.deck_name, used);
    available := available - used;
  END LOOP;
  RETURN NEW;
END;
$$;
-- statement-break
CREATE TRIGGER assign_deck_acquisition AFTER INSERT OR UPDATE OF quantity ON user_collections
FOR EACH ROW EXECUTE FUNCTION mtg_assign_acquisition();
-- statement-break
-- Acquisition imports should not repeatedly scan the user's whole collection.
CREATE INDEX user_collections_assignment_name_idx ON user_collections(user_id, mtg_assignment_name(card_name));
-- statement-break
CREATE INDEX deck_cards_assignment_name_idx ON deck_cards(deck_id, mtg_assignment_name(card_name));
