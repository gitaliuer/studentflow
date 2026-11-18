CREATE TABLE IF NOT EXISTS auth_attempts (
    rate_key TEXT PRIMARY KEY,
    failures INTEGER NOT NULL CHECK (failures >= 0),
    window_started INTEGER NOT NULL,
    blocked_until INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_auth_attempts_blocked_until ON auth_attempts(blocked_until);
