-- A printing belongs to a card_sets row by its stable Scryfall set id. The set
-- code remains metadata on card_sets and is no longer duplicated in printings.
ALTER TABLE "card_printings"
  ADD COLUMN "set_id" TEXT;

UPDATE "card_printings" cp
SET "set_id" = cs.id
FROM "card_sets" cs
WHERE lower(cp."set_code") = lower(cs.code);

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM "card_printings" WHERE "set_id" IS NULL) THEN
    RAISE EXCEPTION
      'Cannot normalize card_printings.set_code: at least one printing has no matching card_sets row';
  END IF;
END $$;

ALTER TABLE "card_printings"
  ALTER COLUMN "set_id" SET NOT NULL;

ALTER TABLE "card_printings"
  ADD CONSTRAINT "card_printings_set_fkey"
  FOREIGN KEY ("set_id") REFERENCES "card_sets" ("id")
  ON DELETE CASCADE;

DROP INDEX IF EXISTS "card_printings_setCode_collectorNumber_key";
DROP INDEX IF EXISTS "card_printings_setCode_idx";

ALTER TABLE "card_printings"
  DROP COLUMN "set_code";

CREATE UNIQUE INDEX "card_printings_setId_collectorNumber_key"
  ON "card_printings" ("set_id", "collector_number");
CREATE INDEX "card_printings_setId_idx"
  ON "card_printings" ("set_id");
