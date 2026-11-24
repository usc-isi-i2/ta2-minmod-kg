-- GeoChem rows only; user and entity tables are kept
TRUNCATE sample_element, sample, mineral_inventory_view, dedup_mineral_inventory_view,
mineral_site, dedup_mineral_site, paper, event_log
RESTART IDENTITY CASCADE;
