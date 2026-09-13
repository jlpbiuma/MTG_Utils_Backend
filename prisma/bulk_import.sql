-- Additive, repeatable migration. The replacement uniqueness preserves foil variants.
BEGIN;
ALTER TABLE user_collections ADD COLUMN IF NOT EXISTS is_foil boolean NOT NULL DEFAULT false;
ALTER TABLE user_collections ADD COLUMN IF NOT EXISTS enrichment_key text;
CREATE UNIQUE INDEX IF NOT EXISTS user_collections_user_id_card_scryfall_id_is_foil_key ON user_collections(user_id,card_scryfall_id,is_foil);
DROP INDEX IF EXISTS user_collections_user_id_card_scryfall_id_key;
CREATE INDEX IF NOT EXISTS user_collections_enrichment_key_idx ON user_collections(enrichment_key);
CREATE TABLE IF NOT EXISTS collection_imports (
 id text PRIMARY KEY,user_id text NOT NULL,request_key text NOT NULL,payload_hash text NOT NULL,
 imported_count integer NOT NULL,unique_cards integer NOT NULL,created_at timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS collection_imports_user_id_request_key_key ON collection_imports(user_id,request_key);
CREATE TABLE IF NOT EXISTS collection_import_items (import_id text NOT NULL,job_key text NOT NULL,PRIMARY KEY(import_id,job_key));
CREATE TABLE IF NOT EXISTS enrichment_jobs (
 key text PRIMARY KEY,identifier jsonb NOT NULL,status text NOT NULL DEFAULT 'queued',phase text NOT NULL DEFAULT 'resolve',card jsonb,
 attempts integer NOT NULL DEFAULT 0,next_attempt_at timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
 lease_until timestamp(3),lease_token text,last_error text,updated_at timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS enrichment_jobs_status_next_attempt_at_idx ON enrichment_jobs(status,next_attempt_at);
CREATE TABLE IF NOT EXISTS scryfall_bulk_cards (
 generation text NOT NULL,id text NOT NULL,name_key text NOT NULL,set_code text NOT NULL,collector_number text NOT NULL,
 lang text NOT NULL,oracle_id text,payload jsonb NOT NULL,PRIMARY KEY(generation,id)
);
CREATE INDEX IF NOT EXISTS scryfall_bulk_cards_generation_name_key_lang_idx ON scryfall_bulk_cards(generation,name_key,lang);
CREATE INDEX IF NOT EXISTS scryfall_bulk_cards_generation_set_code_collector_number_lang_idx ON scryfall_bulk_cards(generation,set_code,collector_number,lang);
CREATE TABLE IF NOT EXISTS scryfall_bulk_state (
 kind text PRIMARY KEY,generation text,source_type text,source_updated_at text,lease_until timestamp(3),lease_token text,last_error text,
 updated_at timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP
);
ALTER TABLE scryfall_bulk_state ADD COLUMN IF NOT EXISTS source_type text;
CREATE INDEX IF NOT EXISTS scryfall_bulk_cards_generation_oracle_id_idx ON scryfall_bulk_cards(generation,oracle_id);
COMMIT;
