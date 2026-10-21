ALTER TABLE acquisition_batches
 ADD COLUMN name TEXT,
 ADD COLUMN purchase_origin TEXT NOT NULL DEFAULT 'other',
 ADD COLUMN seller TEXT,
 ADD COLUMN source_url TEXT,
 ADD COLUMN cost_basis TEXT NOT NULL DEFAULT 'paid' CHECK(cost_basis IN ('paid','trend','allocated')),
 ADD COLUMN fee NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK(fee>=0),
 ADD COLUMN annulled_at TIMESTAMP(3),
 ADD COLUMN purchase_key TEXT,
 ADD COLUMN updated_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE acquisition_batches DROP CONSTRAINT acquisition_batches_kind_check;
ALTER TABLE acquisition_batches ADD CONSTRAINT acquisition_batches_kind_check CHECK(kind IN ('prerelease','booster_box','bundle','singles','trade','other','precon','mini_collection','collection'));
ALTER TABLE acquisition_batch_items
 ADD COLUMN card_name TEXT,
 ADD COLUMN reference_unit_price NUMERIC(14,6),
 ADD COLUMN reference_observed_on DATE,
 ADD COLUMN reference_captured_at TIMESTAMP(3),
 ADD COLUMN cost_amount NUMERIC(12,2),
 ADD COLUMN cost_basis TEXT NOT NULL DEFAULT 'paid',
 ADD COLUMN acquired_on DATE,
 ADD COLUMN metadata JSONB NOT NULL DEFAULT '{}';
UPDATE acquisition_batch_items i SET card_name=COALESCE(
 (SELECT c.card_name FROM user_collections c WHERE c.id=i.collection_card_id),
 (SELECT cat.name FROM card_printings p JOIN card_catalog cat ON cat.id=p.catalog_id WHERE p.id=i.printing_id)),
 acquired_on=(SELECT b.acquired_on FROM acquisition_batches b WHERE b.id=i.batch_id);
UPDATE acquisition_batches b SET name=COALESCE(b.notes, 'Compra'),
 purchase_origin=COALESCE((SELECT s.origin FROM simulated_collections s WHERE s.id=b.offer_id),'other');
CREATE INDEX acquisition_batches_user_date_idx ON acquisition_batches(user_id,acquired_on);
CREATE INDEX acquisition_batches_purchase_key_idx ON acquisition_batches(user_id,purchase_key);
CREATE TABLE acquisition_quotes (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL, mode TEXT NOT NULL,
 payload JSONB NOT NULL, fingerprint TEXT NOT NULL,
 created_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
 consumed_batch_id TEXT REFERENCES acquisition_batches(id) ON DELETE SET NULL
);
CREATE INDEX acquisition_quotes_user_id_idx ON acquisition_quotes(user_id);

ALTER TABLE acquisition_batch_items ADD COLUMN purchase_amount NUMERIC(12,2);
