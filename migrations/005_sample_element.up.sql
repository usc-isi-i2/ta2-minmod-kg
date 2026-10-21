-- Elements each GeoChem sample has readings for, so papers can be searched
-- by measured element. Idempotent. Fill it afterwards with
-- python -m minmodkg.etl.geochem_sample_element
BEGIN;

CREATE TABLE IF NOT EXISTS sample_element (
    sample_id INTEGER NOT NULL,
    site_id VARCHAR NOT NULL,
    element VARCHAR NOT NULL,
    is_bdl BOOLEAN NOT NULL,
    PRIMARY KEY (sample_id, element, is_bdl),
    FOREIGN KEY (sample_id) REFERENCES sample (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_sample_element_site_id_element_is_bdl
ON sample_element (site_id, element, is_bdl);

COMMIT;
