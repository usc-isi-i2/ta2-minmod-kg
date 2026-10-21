-- Which migrations a database has. Read-only.
SELECT
    '001_geochem_sample' AS migration,
    exists(
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'sample' AND column_name = 'deleted_at'
    ) AS applied
UNION ALL
SELECT
    '002_search_indexes' AS migration,
    (
        SELECT count(*) = 3 FROM pg_indexes
        WHERE indexname IN (
            'ix_dedup_mineral_site_country_val_gin',
            'ix_dedup_mineral_site_state_or_province_val_gin',
            'ix_mineral_inventory_view_site_id_commodity'
        )
    ) AS applied
UNION ALL
SELECT
    '003_geochem_sync' AS migration,
    exists(
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'event_log' AND column_name = 'geochem_synced'
    ) AS applied
UNION ALL
SELECT
    '004_paper_registered_by' AS migration,
    exists(
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'paper' AND column_name = 'registered_by'
    ) AS applied
UNION ALL
SELECT
    '005_sample_element' AS migration,
    exists(
        SELECT 1 FROM pg_indexes
        WHERE indexname = 'ix_sample_element_site_id_element_is_bdl'
    ) AS applied;
