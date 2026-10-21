-- Exact bilingual offer lookups on the shared catalog, no user-data changes.
CREATE INDEX IF NOT EXISTS card_catalog_offer_name_idx ON card_catalog (regexp_replace(translate(lower(name), 'áéíóúüñàèìòùâêîôûäëïöüç''’', 'aeiouunaeiouaeiouaeiouc'), '[^a-z0-9]+', '', 'g'));
CREATE INDEX IF NOT EXISTS card_catalog_offer_name_es_idx ON card_catalog (regexp_replace(translate(lower(coalesce(name_es, '')), 'áéíóúüñàèìòùâêîôûäëïöüç''’', 'aeiouunaeiouaeiouaeiouc'), '[^a-z0-9]+', '', 'g'));
CREATE INDEX IF NOT EXISTS card_catalog_offer_front_idx ON card_catalog (regexp_replace(translate(lower(split_part(name, ' // ', 1)), 'áéíóúüñàèìòùâêîôûäëïöüç''’', 'aeiouunaeiouaeiouaeiouc'), '[^a-z0-9]+', '', 'g')) WHERE strpos(name, ' // ') > 0;
CREATE INDEX IF NOT EXISTS card_catalog_offer_front_es_idx ON card_catalog (regexp_replace(translate(lower(split_part(name_es, ' // ', 1)), 'áéíóúüñàèìòùâêîôûäëïöüç''’', 'aeiouunaeiouaeiouaeiouc'), '[^a-z0-9]+', '', 'g')) WHERE strpos(name_es, ' // ') > 0;
CREATE INDEX IF NOT EXISTS card_catalog_offer_printed_es_idx ON card_catalog (regexp_replace(translate(lower(coalesce(details_es->>'printed_name', '')), 'áéíóúüñàèìòùâêîôûäëïöüç''’', 'aeiouunaeiouaeiouaeiouc'), '[^a-z0-9]+', '', 'g'));
CREATE INDEX IF NOT EXISTS card_catalog_offer_faces_es_idx ON card_catalog (id) WHERE jsonb_typeof(details_es->'card_faces') = 'array';
