CREATE TABLE IF NOT EXISTS external_cache (
  cache_key TEXT PRIMARY KEY,
  payload_json TEXT NOT NULL,
  fetched_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_external_cache_fetched_at
ON external_cache(fetched_at);
