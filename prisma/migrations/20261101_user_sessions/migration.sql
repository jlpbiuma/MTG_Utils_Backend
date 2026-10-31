-- Persistent login sessions (hashed tokens), so sessions survive API restarts
-- and requests no longer need the unauthenticated X-User-Id fallback.
-- New table only; no existing data changes.
CREATE TABLE IF NOT EXISTS "user_sessions" (
    "token_hash" TEXT NOT NULL,
    "refresh_hash" TEXT NOT NULL,
    "user_id" TEXT NOT NULL,
    "expires_at" TIMESTAMP(3) NOT NULL,
    "refresh_expires_at" TIMESTAMP(3) NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "user_sessions_pkey" PRIMARY KEY ("token_hash"),
    CONSTRAINT "user_sessions_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE
);
-- statement-break
CREATE UNIQUE INDEX IF NOT EXISTS "user_sessions_refresh_hash_key" ON "user_sessions"("refresh_hash");
-- statement-break
CREATE INDEX IF NOT EXISTS "user_sessions_user_id_idx" ON "user_sessions"("user_id");
