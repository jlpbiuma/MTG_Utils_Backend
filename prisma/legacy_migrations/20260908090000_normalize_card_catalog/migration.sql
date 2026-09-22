-- Card identity/localized details stay in card_catalog for the moment; price
-- data is owned exclusively by card_printings and card_price_history.
ALTER TABLE "card_catalog"
  DROP COLUMN IF EXISTS "price_cardmarket_trend",
  DROP COLUMN IF EXISTS "price_cardmarket_min",
  DROP COLUMN IF EXISTS "price_cardmarket_max",
  DROP COLUMN IF EXISTS "price_cardtrader_trend",
  DROP COLUMN IF EXISTS "price_cardtrader_min",
  DROP COLUMN IF EXISTS "price_cardtrader_max",
  DROP COLUMN IF EXISTS "price_goldfish_trend",
  DROP COLUMN IF EXISTS "price_goldfish_min",
  DROP COLUMN IF EXISTS "price_goldfish_max",
  DROP COLUMN IF EXISTS "prices_updated_at";
