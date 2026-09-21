-- MTG Utils: Índices y Extensiones para Optimización de Rendimiento
-- Habilitar extensión pg_trgm para búsquedas y autocompletado ultra-rápidos
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Índices GIN trigram en card_catalog para búsqueda y autocompletado en < 2ms
CREATE INDEX IF NOT EXISTS card_catalog_search_name_trgm_idx ON card_catalog USING gin (
  regexp_replace(translate(lower(name), 'áéíóúüñàèìòùâêîôûäëïöç', 'aeiouunaeiouaeiouaeiouc'), '[^a-z0-9]+', '', 'g') gin_trgm_ops
);

CREATE INDEX IF NOT EXISTS card_catalog_search_name_es_trgm_idx ON card_catalog USING gin (
  regexp_replace(translate(lower(coalesce(name_es, '')), 'áéíóúüñàèìòùâêîôûäëïöç', 'aeiouunaeiouaeiouaeiouc'), '[^a-z0-9]+', '', 'g') gin_trgm_ops
);

-- Índices B-tree en card_printings para worker de precios y backfill de imágenes
CREATE INDEX IF NOT EXISTS card_printings_prices_updated_at_idx ON card_printings (prices_updated_at);
CREATE INDEX IF NOT EXISTS card_printings_prices_updated_at_nulls_first_idx ON card_printings (prices_updated_at ASC NULLS FIRST);
CREATE INDEX IF NOT EXISTS card_printings_updated_at_idx ON card_printings (updated_at);

-- Índice B-tree en card_catalog para sincronización periódica de reglas y traducciones
CREATE INDEX IF NOT EXISTS card_catalog_updated_at_idx ON card_catalog (updated_at);

-- Índice B-tree compuesto en user_collections para filtrado y paginación rápida por nombre
CREATE INDEX IF NOT EXISTS user_collections_user_id_card_name_idx ON user_collections (user_id, card_name);

-- Índice B-tree en deck_cards para cruce de asignaciones y demanda entre mazos
CREATE INDEX IF NOT EXISTS deck_cards_card_scryfall_id_idx ON deck_cards (card_scryfall_id);
