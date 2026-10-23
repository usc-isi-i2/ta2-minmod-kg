-- Problem 5 acceptance: the commodity corrections list
-- (ta2-minmod-data data/corrections/commodity.csv, applied by the merge).
-- Read-only: plain SELECTs. Run against the kgrel Postgres, once before the
-- deploy and once after the ETL has rebuilt the merged tables:
--
--   PGOPTIONS='-c default_transaction_read_only=on' psql "$DATABASE_URL" \
--     -f acceptance/problem5_commodity_corrections.sql
--
-- One row per metric: metric, value, expected. `expected` is the value after
-- the deploy, `before` the value before it, both measured on a local load of
-- ta2-minmod-data 0c8b827 plus the list. Newer data can drift a little; the
-- 0 rows should stay 0 and the "unchanged" rows should equal the before run.
--
-- If the statickg workdir's merge cache is not merge-v109 (or later)
-- (services/mineralsiteetl/merge-v*.sqlite), the merge did not re-run and
-- nothing here will have changed.
--
-- SQL, not SPARQL: the merged commodity lists live only in Postgres
-- (dedup_mineral_inventory_view, one row per commodity per merged site).
-- mineral_site.inventories is bytea holding orjson of the record's raw
-- commodity candidates (observed_name, normalized_uri, confidence, source).

WITH
-- 1. raw candidates the list corrects: (source, observed name, wrong QID)
listed (sort, source_id, observed, qid, metric, expected) AS (
    VALUES
    (101, 'https://mrdata.usgs.gov/mrds', 'Construction', 'Q609', 'raw: MRDS Construction -> Strontium', '0 (before 46,221)'),
    (102, 'https://mrdata.usgs.gov/mrds', 'Metal', 'Q581', 'raw: MRDS Metal -> Nonmetal', '0 (before 840)'),
    (103, 'https://mrdata.usgs.gov/mrds', 'Water', 'Q659', 'raw: MRDS Water -> Slate', '0 (before 221)'),
    (104, 'https://mrdata.usgs.gov/mrds', 'Volcanic Materials', 'Q523', 'raw: MRDS Volcanic Materials -> Clay > Bloating Material', '0 (before 487)'),
    (105, 'https://mrdata.usgs.gov/mrds', 'Industrial', 'Q559', 'raw: MRDS Industrial -> Indium', '0 (before 11)'),
    (106, 'https://mrdata.usgs.gov/mrds', 'Soda Ash', 'Q619', 'raw: MRDS Soda Ash -> Ash', '0 (before 8)'),
    (107, 'https://mrdata.usgs.gov/mrds', 'Tripoli', 'Q569', 'raw: MRDS Tripoli -> Lithium', '0 (before 1)'),
    (108, 'https://mrdata.usgs.gov/mrds', 'Gilsonite', 'Q636', 'raw: MRDS Gilsonite -> Wollastonite', '0 (before 1)'),
    (111, 'https://www.deq.nc.gov/about/divisions/energy-mineral-and-land-resources/nc-geological-survey', 'Construc', 'Q537', 'raw: NC Construc -> Cobalt', '0 (before 980)'),
    (112, 'https://www.deq.nc.gov/about/divisions/energy-mineral-and-land-resources/nc-geological-survey', 'Metal', 'Q581', 'raw: NC Metal -> Nonmetal', '0 (before 76)'),
    (113, 'https://www.deq.nc.gov/about/divisions/energy-mineral-and-land-resources/nc-geological-survey', 'Halloysite', 'Q10004', 'raw: NC Halloysite -> Alunite', '0 (before 1)'),
    (121, 'http://www.oregongeology.org', 'SC', 'Q607', 'raw: Oregon SC -> Scandium', '2 (before 7,076; left: sand and gravel | SC)'),
    (122, 'http://www.oregongeology.org', 'S', 'Q633', 'raw: Oregon S -> Sulfur', '12 (before 2,349; left: 11 stone | crushed | S | G + Last Chance)'),
    (123, 'http://www.oregongeology.org', 'CS', 'Q591', 'raw: Oregon CS -> Cesium', '0 (before 6)'),
    (124, 'http://www.oregongeology.org', 'SD', 'Q655', 'raw: Oregon SD -> Sand', '0 (before 41)')
),

raw AS MATERIALIZED (
    SELECT
        ms.id,
        ms.source_id,
        inv.v -> 'commodity' ->> 'observed_name' AS observed,
        inv.v -> 'commodity' ->> 'normalized_uri' AS uri
    FROM mineral_site AS ms
    CROSS JOIN LATERAL jsonb_array_elements(
        convert_from(ms.inventories, 'UTF8')::jsonb
    ) AS inv (v)
    WHERE ms.source_id IN (SELECT DISTINCT l.source_id FROM listed AS l)
),

-- 2. and 3. merged sites listing a commodity
merged (sort, qid, metric, expected) AS (
    VALUES
    (201, 'Q609', 'merged sites listing Strontium (Q609)', '393 (before 46,612)'),
    (202, 'Q607', 'merged sites listing Scandium (Q607)', '51 (before 2,998)'),
    (203, 'Q633', 'merged sites listing Sulfur (Q633)', '1,837 (before 4,174)'),
    (204, 'Q537', 'merged sites listing Cobalt (Q537)', '3,062 (before 4,042)'),
    (205, 'Q591', 'merged sites listing Cesium (Q591)', '36 (before 42)'),
    (206, 'Q655', 'merged sites listing Sand (Q655)', '647 (before 688)'),
    (207, 'Q581', 'merged sites listing Nonmetal (Q581)', '3 (before 824)'),
    (301, 'Q664', 'merged sites listing Stone > Crushed/Broken (Q664)', '20,643 (before 17,689)'),
    (302, 'Q665', 'merged sites listing Stone > Dimension (Q665)', '385 (before 344)'),
    (303, 'Q674', 'merged sites listing Titanium > Metal (Q674)', '821 (before 774)'),
    (304, 'Q680', 'merged sites listing Water (Q680)', '221 (before 1)')
),

-- 5. records that must keep a listed commodity
keepers (sort, site_id, qid, metric, expected) AS (
    VALUES
    (501, 'site__mrdata-usgs-gov-mrds__10186268__umn', 'Q609', 'keeper: MRDS 10186268 Strontium Group still lists Strontium', '1'),
    (502, 'site__www-oregongeology-org__id-04514__umn', 'Q633', 'keeper: Oregon ID_04514 Last Chance Sulfur Mine still lists Sulfur', '1')
),

metrics AS (
    SELECT
        l.sort,
        l.metric,
        count(DISTINCT r.id) AS value,
        l.expected
    FROM listed AS l
    LEFT JOIN raw AS r
        ON
            l.source_id = r.source_id
            AND l.observed = r.observed
            AND r.uri = 'https://minmod.isi.edu/resource/' || l.qid
    GROUP BY l.sort, l.metric, l.expected

    UNION ALL

    SELECT
        m.sort,
        m.metric,
        count(DISTINCT v.dedup_site_id) AS value,
        m.expected
    FROM merged AS m
    LEFT JOIN dedup_mineral_inventory_view AS v ON m.qid = v.commodity
    GROUP BY m.sort, m.metric, m.expected

    UNION ALL

    -- 4. sanity: must not move
    SELECT
        401,
        'merged sites total',
        (SELECT count(*) FROM dedup_mineral_site),
        'unchanged (419,098)'
    UNION ALL
    SELECT
        402,
        'raw sites total',
        (SELECT count(*) FROM mineral_site),
        'unchanged (679,778)'
    UNION ALL
    SELECT
        403,
        'merged sites with a country',
        (SELECT count(*) FROM dedup_mineral_site WHERE cardinality(country_val) > 0),
        'unchanged (402,429)'
    UNION ALL
    SELECT
        404,
        'merged sites with a state',
        (
            SELECT count(*) FROM dedup_mineral_site
            WHERE cardinality(state_or_province_val) > 0
        ),
        'unchanged (374,202)'
    UNION ALL
    SELECT
        405,
        'merged sites with no commodity',
        (
            SELECT count(*) FROM dedup_mineral_site AS d
            WHERE NOT EXISTS (
                SELECT 1 FROM dedup_mineral_inventory_view AS v
                WHERE v.dedup_site_id = d.id
            )
        ),
        'must not go up (44,884)'

    UNION ALL

    SELECT
        k.sort,
        k.metric,
        count(v.id) AS value,
        k.expected
    FROM keepers AS k
    LEFT JOIN mineral_site AS ms ON k.site_id = ms.site_id
    LEFT JOIN dedup_mineral_inventory_view AS v
        ON ms.dedup_site_id = v.dedup_site_id AND k.qid = v.commodity
    GROUP BY k.sort, k.metric, k.expected
)

SELECT
    metric,
    value,
    expected
FROM metrics
ORDER BY sort;
