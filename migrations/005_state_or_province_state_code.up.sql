-- State codes for the merge-time state repair. Idempotent; run before the new
-- API starts. Existing rows stay NULL (the code tier never hits) until the next
-- data load fills them from ta2-minmod-data's state_or_province.csv.
BEGIN;

ALTER TABLE state_or_province ADD COLUMN IF NOT EXISTS state_code VARCHAR;

COMMIT;
