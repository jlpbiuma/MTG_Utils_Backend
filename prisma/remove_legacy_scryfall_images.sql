-- Direct Scryfall CDN URLs are legacy metadata. Originals are mirrored to
-- MinIO by the worker and public clients must never receive the upstream URL.
UPDATE "card_catalog"
SET "image_uri" = NULL
WHERE "image_uri" LIKE 'https://cards.scryfall.io/%';

UPDATE "deck_cards"
SET "image_uri" = NULL
WHERE "image_uri" LIKE 'https://cards.scryfall.io/%';

UPDATE "user_collections"
SET "image_uri" = NULL
WHERE "image_uri" LIKE 'https://cards.scryfall.io/%';

UPDATE "decks"
SET "commander_image_uri" = NULL
WHERE "commander_image_uri" LIKE 'https://cards.scryfall.io/%';
