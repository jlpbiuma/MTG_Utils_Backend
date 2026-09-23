-- CreateTable
CREATE TABLE "edhrec_commanders" (
    "id" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "normalized_name" TEXT NOT NULL,
    "slug" TEXT NOT NULL,
    "color_identity" TEXT NOT NULL DEFAULT '',
    "rank" INTEGER,
    "is_top_100" BOOLEAN NOT NULL DEFAULT false,
    "num_decks" INTEGER NOT NULL DEFAULT 0,
    "creature_count" INTEGER NOT NULL DEFAULT 0,
    "instant_count" INTEGER NOT NULL DEFAULT 0,
    "sorcery_count" INTEGER NOT NULL DEFAULT 0,
    "artifact_count" INTEGER NOT NULL DEFAULT 0,
    "enchantment_count" INTEGER NOT NULL DEFAULT 0,
    "battle_count" INTEGER NOT NULL DEFAULT 0,
    "planeswalker_count" INTEGER NOT NULL DEFAULT 0,
    "land_count" INTEGER NOT NULL DEFAULT 0,
    "basic_land_count" INTEGER NOT NULL DEFAULT 0,
    "nonbasic_land_count" INTEGER NOT NULL DEFAULT 0,
    "cards_json" JSONB,
    "canonical_card_names" JSONB,
    "status" TEXT NOT NULL DEFAULT 'pending',
    "last_error" TEXT,
    "synced_at" TIMESTAMP(3),
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "edhrec_commanders_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "edhrec_commanders_name_key" ON "edhrec_commanders"("name");
CREATE UNIQUE INDEX "edhrec_commanders_normalized_name_key" ON "edhrec_commanders"("normalized_name");
CREATE UNIQUE INDEX "edhrec_commanders_slug_key" ON "edhrec_commanders"("slug");
CREATE INDEX "edhrec_commanders_is_top_100_idx" ON "edhrec_commanders"("is_top_100");
CREATE INDEX "edhrec_commanders_status_idx" ON "edhrec_commanders"("status");
CREATE INDEX "edhrec_commanders_synced_at_idx" ON "edhrec_commanders"("synced_at");
