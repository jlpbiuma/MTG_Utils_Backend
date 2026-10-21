ALTER TABLE "simulated_collections"
 ADD COLUMN "origin" TEXT NOT NULL DEFAULT 'other',
 ADD COLUMN "opportunity_subject" TEXT NOT NULL DEFAULT 'vende',
 ADD COLUMN "source_metadata" JSONB;
