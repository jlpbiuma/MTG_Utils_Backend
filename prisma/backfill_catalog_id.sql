-- Backfill card_printings.catalog_id from card_catalog before `prisma db push`
-- drops the edition-agnostic columns (normalized_name, etc.) that are now owned
-- by card_catalog. Idempotent and safe to run on every container start.
ALTER TABLE "card_printings" ADD COLUMN IF NOT EXISTS "catalog_id" TEXT;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'card_printings'
          AND column_name = 'normalized_name'
    ) THEN
        UPDATE "card_printings" cp
        SET "catalog_id" = cc.id
        FROM "card_catalog" cc
        WHERE cc."normalized_name" = cp."normalized_name"
          AND cp."catalog_id" IS NULL;
    END IF;
END $$;