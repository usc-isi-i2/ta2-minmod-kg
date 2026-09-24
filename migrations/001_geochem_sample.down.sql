-- Reverts 001_geochem_sample.up.sql. Destroys all GeoChem sample data.
BEGIN;

DROP TABLE IF EXISTS sample;
ALTER TABLE mineral_site DROP COLUMN IF EXISTS is_deleted;
ALTER TABLE mineral_site DROP COLUMN IF EXISTS deleted_by;
ALTER TABLE mineral_site DROP COLUMN IF EXISTS deleted_at;
DELETE FROM event_log
WHERE type IN ('sample:add', 'sample:update');

COMMIT;
