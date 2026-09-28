-- Normalize existing COMNAP public facility keys to the source Record ID.
-- This makes future syncs update the same facility even when a name, status,
-- coordinate or operator field changes in the authoritative dataset.
UPDATE public_facilities
SET source_key = 'comnap:' || CAST(json_extract(raw_json, '$."Record ID#"') AS TEXT)
WHERE source = 'COMNAP'
  AND json_extract(raw_json, '$."Record ID#"') IS NOT NULL
  AND trim(CAST(json_extract(raw_json, '$."Record ID#"') AS TEXT)) <> '';
