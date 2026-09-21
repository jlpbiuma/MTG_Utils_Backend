-- Purge legacy simulated cardtrader price history
DELETE FROM card_price_history WHERE provider = 'cardtrader';

-- Drop obsolete cardtrader columns from card_printings if they exist
ALTER TABLE card_printings
  DROP COLUMN IF EXISTS price_cardtrader_trend,
  DROP COLUMN IF EXISTS price_cardtrader_min,
  DROP COLUMN IF EXISTS price_cardtrader_max;
