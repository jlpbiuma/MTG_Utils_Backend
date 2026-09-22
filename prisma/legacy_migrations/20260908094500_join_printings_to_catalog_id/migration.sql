-- Join card_printings to card_catalog by the catalog entry id (Scryfall card id)
-- instead of the edition-agnostic normalized name. Edition-agnostic card data
-- (card name, mana cost, type line, localized fields) moves into card_catalog
-- and is resolved through this foreign key.

-- 1. Add the catalog_id column to card_printings.
ALTER TABLE "card_printings"
  ADD COLUMN "catalog_id" TEXT;

-- 2. Backfill catalog_id for existing printings: match the catalog entry whose
--    normalized_name equals the printing's normalized_name.
UPDATE "card_printings" cp
SET "catalog_id" = cc.id
FROM "card_catalog" cc
WHERE cc."normalized_name" = cp."normalized_name";

-- 3. Add image_uri_large for high-resolution WebP output.
ALTER TABLE "card_printings"
  ADD COLUMN "image_uri_large" TEXT;

-- 4. Drop the now-redundant columns that are owned by card_catalog.
ALTER TABLE "card_printings"
  DROP COLUMN IF EXISTS "card_name",
  DROP COLUMN IF EXISTS "normalized_name",
  DROP COLUMN IF EXISTS "mana_cost",
  DROP COLUMN IF EXISTS "type_line",
  DROP COLUMN IF EXISTS "name_es",
  DROP COLUMN IF EXISTS "type_line_es",
  DROP COLUMN IF EXISTS "oracle_text_es",
  DROP COLUMN IF EXISTS "flavor_text_es",
  DROP COLUMN IF EXISTS "details_es";

-- 5. Drop the normalized-name index and add an index on the FK.
DROP INDEX IF EXISTS "card_printings_normalizedName_idx";
CREATE INDEX "card_printings_catalogId_idx" ON "card_printings" ("catalog_id");

-- 6. Foreign key: a printing belongs to exactly one catalog entry.
ALTER TABLE "card_printings"
  ADD CONSTRAINT "card_printings_catalog_fkey"
  FOREIGN KEY ("catalog_id") REFERENCES "card_catalog" ("id")
  ON DELETE SET NULL;