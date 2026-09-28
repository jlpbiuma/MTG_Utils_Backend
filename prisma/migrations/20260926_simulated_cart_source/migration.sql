-- Preserve the original cart, including purchase prices and printing hints.
ALTER TABLE "simulated_collections" ADD COLUMN "raw_text" TEXT;
