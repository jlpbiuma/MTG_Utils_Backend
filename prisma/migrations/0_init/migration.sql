-- CreateTable
CREATE TABLE "decks" (
    "id" TEXT NOT NULL,
    "user_id" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "format" TEXT NOT NULL DEFAULT 'Commander',
    "description" TEXT,
    "commander" TEXT,
    "commander_scryfall_id" TEXT,
    "commander_image_uri" TEXT,
    "is_archived" BOOLEAN NOT NULL DEFAULT false,
    "tags" TEXT DEFAULT '',
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "decks_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "deck_cards" (
    "id" TEXT NOT NULL,
    "deck_id" TEXT NOT NULL,
    "card_scryfall_id" TEXT NOT NULL,
    "card_name" TEXT NOT NULL,
    "quantity" INTEGER NOT NULL DEFAULT 1,
    "assigned_quantity" INTEGER NOT NULL DEFAULT 0,
    "is_sideboard" BOOLEAN NOT NULL DEFAULT false,
    "is_commander" BOOLEAN NOT NULL DEFAULT false,
    "mana_cost" TEXT,
    "type_line" TEXT,
    "image_uri" TEXT,
    "set_code" TEXT,
    "tags" TEXT DEFAULT '',

    CONSTRAINT "deck_cards_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "user_collections" (
    "id" TEXT NOT NULL,
    "user_id" TEXT NOT NULL,
    "card_scryfall_id" TEXT NOT NULL,
    "card_name" TEXT NOT NULL,
    "is_foil" BOOLEAN NOT NULL DEFAULT false,
    "enrichment_key" TEXT,
    "quantity" INTEGER NOT NULL DEFAULT 1,
    "set_code" TEXT,
    "collector_number" TEXT,
    "mana_cost" TEXT,
    "type_line" TEXT,
    "image_uri" TEXT,
    "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "user_collections_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "user_wants" (
    "id" TEXT NOT NULL,
    "user_id" TEXT NOT NULL,
    "card_scryfall_id" TEXT NOT NULL,
    "card_name" TEXT NOT NULL,
    "quantity" INTEGER NOT NULL DEFAULT 1,
    "set_code" TEXT,
    "collector_number" TEXT,
    "mana_cost" TEXT,
    "type_line" TEXT,
    "image_uri" TEXT,
    "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "user_wants_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "card_catalog" (
    "id" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "normalized_name" TEXT NOT NULL,
    "mana_cost" TEXT,
    "type_line" TEXT,
    "oracle_text_es" TEXT,
    "name_es" TEXT,
    "type_line_es" TEXT,
    "flavor_text_es" TEXT,
    "details_es" JSONB,
    "details_updated_at" TIMESTAMP(3),
    "image_uri" TEXT,
    "set_code" TEXT,
    "collector_number" TEXT,
    "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "card_catalog_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "users" (
    "id" TEXT NOT NULL,
    "email" TEXT NOT NULL,
    "name" TEXT,
    "password_hash" TEXT NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "users_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "card_sets" (
    "id" TEXT NOT NULL,
    "code" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "set_type" TEXT NOT NULL,
    "card_count" INTEGER NOT NULL DEFAULT 0,
    "released_at" TIMESTAMP(3),
    "icon_svg_uri" TEXT,
    "search_uri" TEXT NOT NULL,
    "is_digital" BOOLEAN NOT NULL DEFAULT false,
    "is_downloaded" BOOLEAN NOT NULL DEFAULT false,
    "downloaded_at" TIMESTAMP(3),
    "download_attempts" INTEGER NOT NULL DEFAULT 0,
    "last_attempt_at" TIMESTAMP(3),
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "card_sets_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "card_printings" (
    "id" TEXT NOT NULL,
    "catalog_id" TEXT,
    "set_id" TEXT NOT NULL,
    "collector_number" TEXT NOT NULL,
    "rarity" TEXT,
    "image_uri" TEXT,
    "image_uri_small" TEXT,
    "image_uri_large" TEXT,
    "price_eur" DOUBLE PRECISION,
    "price_eur_foil" DOUBLE PRECISION,
    "price_usd" DOUBLE PRECISION,
    "price_usd_foil" DOUBLE PRECISION,
    "price_cardmarket_trend" DOUBLE PRECISION,
    "price_cardmarket_min" DOUBLE PRECISION,
    "price_cardmarket_max" DOUBLE PRECISION,
    "prices_updated_at" TIMESTAMP(3),
    "released_at" TIMESTAMP(3),
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "card_printings_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "card_price_history" (
    "id" TEXT NOT NULL,
    "card_printing_id" TEXT NOT NULL,
    "provider" TEXT NOT NULL DEFAULT 'cardmarket',
    "currency" TEXT NOT NULL DEFAULT 'EUR',
    "trend_price" DOUBLE PRECISION,
    "min_price" DOUBLE PRECISION,
    "max_price" DOUBLE PRECISION,
    "price_eur" DOUBLE PRECISION,
    "price_eur_foil" DOUBLE PRECISION,
    "price_usd" DOUBLE PRECISION,
    "price_usd_foil" DOUBLE PRECISION,
    "recorded_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "card_price_history_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "card_translation_retries" (
    "card_printing_id" TEXT NOT NULL,
    "attempts" INTEGER NOT NULL DEFAULT 0,
    "next_attempt_at" TIMESTAMP(3) NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "card_translation_retries_pkey" PRIMARY KEY ("card_printing_id")
);

-- CreateTable
CREATE TABLE "rule_documents" (
    "id" TEXT NOT NULL,
    "source" TEXT NOT NULL,
    "format" TEXT NOT NULL DEFAULT 'txt',
    "url" TEXT NOT NULL,
    "effective_date" TIMESTAMP(3),
    "etag" TEXT,
    "last_modified" TEXT,
    "sha256" TEXT NOT NULL,
    "content" TEXT NOT NULL,
    "fetched_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "rule_documents_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "rule_document_changes" (
    "id" TEXT NOT NULL,
    "previous_document_id" TEXT,
    "document_id" TEXT NOT NULL,
    "rule_number" TEXT NOT NULL,
    "change_type" TEXT NOT NULL,
    "old_text" TEXT,
    "new_text" TEXT,

    CONSTRAINT "rule_document_changes_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "card_rulings" (
    "id" TEXT NOT NULL,
    "oracle_id" TEXT NOT NULL,
    "scryfall_card_id" TEXT NOT NULL,
    "source" TEXT NOT NULL,
    "ruling_date" TIMESTAMP(3) NOT NULL,
    "text" TEXT NOT NULL,
    "text_hash" TEXT NOT NULL,
    "first_seen_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "last_seen_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "card_rulings_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "ruling_card_sync" (
    "scryfall_card_id" TEXT NOT NULL,
    "oracle_id" TEXT,
    "last_checked_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "ruling_card_sync_pkey" PRIMARY KEY ("scryfall_card_id")
);

-- CreateTable
CREATE TABLE "rules_sync_state" (
    "id" TEXT NOT NULL DEFAULT 'rules',
    "rulings_cursor" TEXT,
    "last_rules_check_at" TIMESTAMP(3),
    "last_rulings_full_cycle_at" TIMESTAMP(3),
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "rules_sync_state_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "collection_imports" (
    "id" TEXT NOT NULL,
    "user_id" TEXT NOT NULL,
    "request_key" TEXT NOT NULL,
    "payload_hash" TEXT NOT NULL,
    "imported_count" INTEGER NOT NULL,
    "unique_cards" INTEGER NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "collection_imports_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "collection_import_items" (
    "import_id" TEXT NOT NULL,
    "job_key" TEXT NOT NULL,

    CONSTRAINT "collection_import_items_pkey" PRIMARY KEY ("import_id","job_key")
);

-- CreateTable
CREATE TABLE "enrichment_jobs" (
    "key" TEXT NOT NULL,
    "identifier" JSONB NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'queued',
    "phase" TEXT NOT NULL DEFAULT 'resolve',
    "card" JSONB,
    "attempts" INTEGER NOT NULL DEFAULT 0,
    "next_attempt_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "lease_until" TIMESTAMP(3),
    "lease_token" TEXT,
    "last_error" TEXT,
    "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "enrichment_jobs_pkey" PRIMARY KEY ("key")
);

-- CreateTable
CREATE TABLE "scryfall_bulk_cards" (
    "generation" TEXT NOT NULL,
    "id" TEXT NOT NULL,
    "name_key" TEXT NOT NULL,
    "set_code" TEXT NOT NULL,
    "collector_number" TEXT NOT NULL,
    "lang" TEXT NOT NULL,
    "oracle_id" TEXT,
    "payload" JSONB NOT NULL,

    CONSTRAINT "scryfall_bulk_cards_pkey" PRIMARY KEY ("generation","id")
);

-- CreateTable
CREATE TABLE "scryfall_bulk_state" (
    "kind" TEXT NOT NULL,
    "generation" TEXT,
    "source_type" TEXT,
    "source_updated_at" TEXT,
    "lease_until" TIMESTAMP(3),
    "lease_token" TEXT,
    "last_error" TEXT,
    "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "scryfall_bulk_state_pkey" PRIMARY KEY ("kind")
);

-- CreateTable
CREATE TABLE "simulated_collections" (
    "id" TEXT NOT NULL,
    "user_id" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "description" TEXT,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "simulated_collections_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "simulated_cards" (
    "id" TEXT NOT NULL,
    "simulated_collection_id" TEXT NOT NULL,
    "card_scryfall_id" TEXT,
    "card_name" TEXT NOT NULL,
    "quantity" INTEGER NOT NULL DEFAULT 1,
    "set_code" TEXT,
    "collector_number" TEXT,
    "mana_cost" TEXT,
    "type_line" TEXT,
    "image_uri" TEXT,
    "price" DOUBLE PRECISION,

    CONSTRAINT "simulated_cards_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "mtgjson_uuid_map" (
    "uuid" TEXT NOT NULL,
    "scryfall_id" TEXT NOT NULL,

    CONSTRAINT "mtgjson_uuid_map_pkey" PRIMARY KEY ("uuid")
);

-- CreateTable
CREATE TABLE "cm_price_history" (
    "scryfall_id" TEXT NOT NULL,
    "finish" SMALLINT NOT NULL,
    "date" DATE NOT NULL,
    "price_cents" INTEGER NOT NULL,

    CONSTRAINT "cm_price_history_pkey" PRIMARY KEY ("scryfall_id","date","finish")
);

-- CreateTable
CREATE TABLE "price_anomalies" (
    "id" TEXT NOT NULL,
    "scryfall_id" TEXT NOT NULL,
    "finish" SMALLINT NOT NULL,
    "date" DATE NOT NULL,
    "price_cents" INTEGER NOT NULL,
    "prev_cents" INTEGER NOT NULL,
    "detected_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "resolved" BOOLEAN NOT NULL DEFAULT false,

    CONSTRAINT "price_anomalies_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "ingest_runs" (
    "id" SERIAL NOT NULL,
    "source_url" TEXT,
    "feed_date" TEXT,
    "status" TEXT NOT NULL,
    "tracked" INTEGER,
    "mapped" INTEGER,
    "matched" INTEGER,
    "inserted" INTEGER,
    "invalid" INTEGER,
    "anomalies" INTEGER,
    "conflicts" INTEGER,
    "notes" TEXT,
    "error" TEXT,
    "started_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "finished_at" TIMESTAMP(3),

    CONSTRAINT "ingest_runs_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "decks_user_id_idx" ON "decks"("user_id");

-- CreateIndex
CREATE INDEX "deck_cards_deck_id_idx" ON "deck_cards"("deck_id");

-- CreateIndex
CREATE INDEX "deck_cards_card_scryfall_id_idx" ON "deck_cards"("card_scryfall_id");

-- CreateIndex
CREATE UNIQUE INDEX "deck_cards_deck_id_card_scryfall_id_is_sideboard_key" ON "deck_cards"("deck_id", "card_scryfall_id", "is_sideboard");

-- CreateIndex
CREATE INDEX "user_collections_enrichment_key_idx" ON "user_collections"("enrichment_key");

-- CreateIndex
CREATE INDEX "user_collections_user_id_idx" ON "user_collections"("user_id");

-- CreateIndex
CREATE INDEX "user_collections_user_id_card_name_idx" ON "user_collections"("user_id", "card_name");

-- CreateIndex
CREATE UNIQUE INDEX "user_collections_user_id_card_scryfall_id_is_foil_key" ON "user_collections"("user_id", "card_scryfall_id", "is_foil");

-- CreateIndex
CREATE INDEX "user_wants_user_id_idx" ON "user_wants"("user_id");

-- CreateIndex
CREATE UNIQUE INDEX "user_wants_user_id_card_scryfall_id_key" ON "user_wants"("user_id", "card_scryfall_id");

-- CreateIndex
CREATE UNIQUE INDEX "card_catalog_name_key" ON "card_catalog"("name");

-- CreateIndex
CREATE UNIQUE INDEX "card_catalog_normalized_name_key" ON "card_catalog"("normalized_name");

-- CreateIndex
CREATE INDEX "card_catalog_updated_at_idx" ON "card_catalog"("updated_at");

-- CreateIndex
CREATE UNIQUE INDEX "users_email_key" ON "users"("email");

-- CreateIndex
CREATE UNIQUE INDEX "card_sets_code_key" ON "card_sets"("code");

-- CreateIndex
CREATE INDEX "card_printings_catalog_id_idx" ON "card_printings"("catalog_id");

-- CreateIndex
CREATE INDEX "card_printings_set_id_idx" ON "card_printings"("set_id");

-- CreateIndex
CREATE INDEX "card_printings_prices_updated_at_idx" ON "card_printings"("prices_updated_at");

-- CreateIndex
CREATE INDEX "card_printings_updated_at_idx" ON "card_printings"("updated_at");

-- CreateIndex
CREATE UNIQUE INDEX "card_printings_set_id_collector_number_key" ON "card_printings"("set_id", "collector_number");

-- CreateIndex
CREATE INDEX "card_price_history_card_printing_id_provider_recorded_at_idx" ON "card_price_history"("card_printing_id", "provider", "recorded_at");

-- CreateIndex
CREATE INDEX "card_translation_retries_next_attempt_at_idx" ON "card_translation_retries"("next_attempt_at");

-- CreateIndex
CREATE INDEX "rule_documents_source_fetched_at_idx" ON "rule_documents"("source", "fetched_at");

-- CreateIndex
CREATE UNIQUE INDEX "rule_documents_source_sha256_key" ON "rule_documents"("source", "sha256");

-- CreateIndex
CREATE INDEX "rule_document_changes_rule_number_idx" ON "rule_document_changes"("rule_number");

-- CreateIndex
CREATE UNIQUE INDEX "rule_document_changes_document_id_rule_number_key" ON "rule_document_changes"("document_id", "rule_number");

-- CreateIndex
CREATE INDEX "card_rulings_oracle_id_ruling_date_idx" ON "card_rulings"("oracle_id", "ruling_date");

-- CreateIndex
CREATE UNIQUE INDEX "card_rulings_oracle_id_text_hash_key" ON "card_rulings"("oracle_id", "text_hash");

-- CreateIndex
CREATE INDEX "ruling_card_sync_last_checked_at_idx" ON "ruling_card_sync"("last_checked_at");

-- CreateIndex
CREATE UNIQUE INDEX "collection_imports_user_id_request_key_key" ON "collection_imports"("user_id", "request_key");

-- CreateIndex
CREATE INDEX "enrichment_jobs_status_next_attempt_at_idx" ON "enrichment_jobs"("status", "next_attempt_at");

-- CreateIndex
CREATE INDEX "scryfall_bulk_cards_generation_name_key_lang_idx" ON "scryfall_bulk_cards"("generation", "name_key", "lang");

-- CreateIndex
CREATE INDEX "scryfall_bulk_cards_generation_oracle_id_idx" ON "scryfall_bulk_cards"("generation", "oracle_id");

-- CreateIndex
CREATE INDEX "scryfall_bulk_cards_generation_set_code_collector_number_la_idx" ON "scryfall_bulk_cards"("generation", "set_code", "collector_number", "lang");

-- CreateIndex
CREATE INDEX "simulated_collections_user_id_idx" ON "simulated_collections"("user_id");

-- CreateIndex
CREATE INDEX "simulated_cards_simulated_collection_id_idx" ON "simulated_cards"("simulated_collection_id");

-- CreateIndex
CREATE INDEX "mtgjson_uuid_map_scryfall_id_idx" ON "mtgjson_uuid_map"("scryfall_id");

-- CreateIndex
CREATE INDEX "cm_price_history_date_idx" ON "cm_price_history"("date");

-- CreateIndex
CREATE UNIQUE INDEX "price_anomalies_scryfall_id_finish_date_key" ON "price_anomalies"("scryfall_id", "finish", "date");

-- AddForeignKey
ALTER TABLE "deck_cards" ADD CONSTRAINT "deck_cards_deck_id_fkey" FOREIGN KEY ("deck_id") REFERENCES "decks"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "card_printings" ADD CONSTRAINT "card_printings_catalog_id_fkey" FOREIGN KEY ("catalog_id") REFERENCES "card_catalog"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "card_printings" ADD CONSTRAINT "card_printings_set_id_fkey" FOREIGN KEY ("set_id") REFERENCES "card_sets"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "card_price_history" ADD CONSTRAINT "card_price_history_card_printing_id_fkey" FOREIGN KEY ("card_printing_id") REFERENCES "card_printings"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "card_translation_retries" ADD CONSTRAINT "card_translation_retries_card_printing_id_fkey" FOREIGN KEY ("card_printing_id") REFERENCES "card_printings"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "simulated_cards" ADD CONSTRAINT "simulated_cards_simulated_collection_id_fkey" FOREIGN KEY ("simulated_collection_id") REFERENCES "simulated_collections"("id") ON DELETE CASCADE ON UPDATE CASCADE;
