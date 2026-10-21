CREATE TABLE acquisition_batches (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL, set_code TEXT, kind TEXT NOT NULL CHECK (kind IN ('prerelease','booster_box','bundle','singles','trade','other')),
 acquired_on DATE NOT NULL, cost_total NUMERIC(12,2) NOT NULL CHECK (cost_total >= 0), shipping NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (shipping >= 0),
 currency TEXT NOT NULL DEFAULT 'EUR' CHECK (currency IN ('EUR','USD')), notes TEXT, origin TEXT NOT NULL DEFAULT 'manual', offer_id TEXT UNIQUE, created_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX acquisition_batches_user_set_idx ON acquisition_batches(user_id,set_code);
CREATE TABLE acquisition_batch_items (
 id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES acquisition_batches(id) ON DELETE CASCADE,
 collection_card_id TEXT REFERENCES user_collections(id) ON DELETE SET NULL, printing_id TEXT NOT NULL,
 quantity INTEGER NOT NULL CHECK(quantity > 0), is_foil BOOLEAN NOT NULL DEFAULT false,
 UNIQUE(batch_id,collection_card_id)
);
CREATE INDEX acquisition_batch_items_collection_idx ON acquisition_batch_items(collection_card_id);
CREATE INDEX acquisition_batch_items_printing_idx ON acquisition_batch_items(printing_id);
ALTER TABLE simulated_collections ADD COLUMN status TEXT NOT NULL DEFAULT 'evaluating' CHECK (status IN ('evaluating','purchased','discarded')),
 ADD COLUMN seller TEXT, ADD COLUMN shipping NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (shipping >= 0), ADD COLUMN source_url TEXT,
 ADD COLUMN paid_total NUMERIC(12,2) CHECK (paid_total >= 0), ADD COLUMN confirmed_batch_id TEXT;
CREATE TABLE user_preferences (user_id TEXT PRIMARY KEY, price_provider TEXT NOT NULL DEFAULT 'cardmarket' CHECK (price_provider IN ('cardmarket','cardtrader','mtggoldfish')));
CREATE TABLE set_index_members (set_code TEXT NOT NULL, printing_id TEXT NOT NULL, baseline_price DOUBLE PRECISION NOT NULL, baseline_date DATE NOT NULL,
 definition_version INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(set_code,printing_id,definition_version));
CREATE TABLE set_price_index (set_code TEXT NOT NULL, provider TEXT NOT NULL DEFAULT 'cardmarket', captured_on DATE NOT NULL, index_value DOUBLE PRECISION NOT NULL,
 n_cards INTEGER NOT NULL, definition_version INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(set_code,provider,captured_on,definition_version));
CREATE TABLE deck_function_targets (deck_id TEXT PRIMARY KEY REFERENCES decks(id) ON DELETE CASCADE, targets JSONB NOT NULL);
