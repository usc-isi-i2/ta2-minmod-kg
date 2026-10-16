-- Reverts 005_state_or_province_state_code.up.sql.
BEGIN;

ALTER TABLE state_or_province DROP COLUMN IF EXISTS state_code;

COMMIT;
