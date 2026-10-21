CREATE TABLE "manual_opportunity_cards" (
    "id" SERIAL PRIMARY KEY,
    "user_id" TEXT NOT NULL,
    "source_url" TEXT NOT NULL,
    "subject" TEXT NOT NULL CHECK ("subject" IN ('vende', 'compra_busca')),
    "card_name" TEXT NOT NULL,
    "set_code" TEXT,
    "currency" TEXT,
    "extra" JSONB NOT NULL,
    "imported_at" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX "manual_opportunity_cards_user_id_source_url_idx"
    ON "manual_opportunity_cards" ("user_id", "source_url");
CREATE INDEX "manual_opportunity_cards_user_id_imported_at_idx"
    ON "manual_opportunity_cards" ("user_id", "imported_at");
