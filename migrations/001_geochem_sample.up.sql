-- GeoChem samples + soft delete. Idempotent; run before the new API starts.
BEGIN;

ALTER TABLE mineral_site
ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE mineral_site ADD COLUMN IF NOT EXISTS deleted_by VARCHAR;
ALTER TABLE mineral_site ADD COLUMN IF NOT EXISTS deleted_at VARCHAR;

CREATE TABLE IF NOT EXISTS sample (
    id SERIAL NOT NULL,
    public_id VARCHAR NOT NULL,
    mineral_site_id VARCHAR NOT NULL,
    sample_id VARCHAR,
    sample_name VARCHAR,
    sample_local_id VARCHAR,
    sample_type VARCHAR,
    collection_date VARCHAR,
    description VARCHAR,
    mineral VARCHAR,
    sampling_method VARCHAR,
    sample_preparation VARCHAR,
    material_class VARCHAR,
    material_class_comment VARCHAR,
    analysed_material VARCHAR,
    sample_deposit_relation VARCHAR,
    geological_province VARCHAR,
    strat_unit_uid VARCHAR,
    strat_unit_name VARCHAR,
    strat_grouping VARCHAR,
    earth_material_group VARCHAR,
    earth_material_qualifier VARCHAR,
    mode_occurrence VARCHAR,
    metamorphic_grade VARCHAR,
    alteration VARCHAR,
    paragenetic_stage VARCHAR,
    texture VARCHAR,
    color VARCHAR,
    associated_minerals VARCHAR,
    feature_type VARCHAR,
    feature_name VARCHAR,
    feature_local_uid VARCHAR,
    top_depth_m FLOAT,
    bottom_depth_m FLOAT,
    comments VARCHAR,  -- noqa: RF04
    location BYTEA,  -- noqa: RF04
    is_deleted BOOLEAN NOT NULL DEFAULT false,
    deleted_by VARCHAR,
    deleted_at VARCHAR,
    analyses BYTEA NOT NULL,
    reference BYTEA NOT NULL,  -- noqa: RF04
    edit_history BYTEA NOT NULL,
    modified_at BIGINT NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (public_id),
    FOREIGN KEY (mineral_site_id) REFERENCES mineral_site (
        site_id
    ) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_sample_mineral_site_id ON sample (
    mineral_site_id
);

-- a sample table from an earlier build of this branch predates these columns
ALTER TABLE sample ADD COLUMN IF NOT EXISTS strat_unit_name VARCHAR;
ALTER TABLE sample ADD COLUMN IF NOT EXISTS location BYTEA;
ALTER TABLE sample
ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE sample ADD COLUMN IF NOT EXISTS deleted_by VARCHAR;
ALTER TABLE sample ADD COLUMN IF NOT EXISTS deleted_at VARCHAR;

-- is_deleted may already exist as nullable if it was added by hand
UPDATE mineral_site SET is_deleted = false
WHERE is_deleted IS null;
ALTER TABLE mineral_site ALTER COLUMN is_deleted SET DEFAULT false,
ALTER COLUMN is_deleted SET NOT NULL;
UPDATE sample SET is_deleted = false
WHERE is_deleted IS null;
ALTER TABLE sample ALTER COLUMN is_deleted SET DEFAULT false,
ALTER COLUMN is_deleted SET NOT NULL;

-- event_log.type needs no change: 'sample:add'/'sample:update' fit varchar(14).

COMMIT;
