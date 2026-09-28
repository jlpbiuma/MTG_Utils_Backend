ALTER TABLE "user_collections" ADD COLUMN "acquired_at" TIMESTAMP(3);
ALTER TABLE "user_collections" ALTER COLUMN "acquired_at" SET DEFAULT CURRENT_TIMESTAMP;

ALTER TABLE "simulated_cards" ADD COLUMN "acquired_at" TIMESTAMP(3);
ALTER TABLE "simulated_cards" ALTER COLUMN "acquired_at" SET DEFAULT CURRENT_TIMESTAMP;
