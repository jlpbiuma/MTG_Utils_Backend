-- Prepare the normalized card_printings -> card_sets relation before
-- `prisma db push` removes the legacy set_code column.
BEGIN;

-- A running worker must not insert another legacy row between the backfill and
-- the NOT NULL constraint. This lock is held only for this short migration.
LOCK TABLE "card_printings" IN ACCESS EXCLUSIVE MODE;

ALTER TABLE "card_printings"
  ADD COLUMN IF NOT EXISTS "set_id" TEXT;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name = 'card_printings'
      AND column_name = 'set_code'
  ) THEN
    EXECUTE '
      UPDATE "card_printings" cp
      SET "set_id" = cs.id
      FROM "card_sets" cs
      WHERE cp."set_id" IS NULL
        AND lower(cp."set_code") = lower(cs.code)';
  END IF;

  IF EXISTS (SELECT 1 FROM "card_printings" WHERE "set_id" IS NULL) THEN
    RAISE EXCEPTION
      'Cannot normalize card_printings.set_code: at least one printing has no matching card_sets row';
  END IF;
END $$;

ALTER TABLE "card_printings"
  ALTER COLUMN "set_id" SET NOT NULL;

COMMIT;
