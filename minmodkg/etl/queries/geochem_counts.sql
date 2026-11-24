SELECT
    (SELECT count(*) FROM paper) AS papers,
    (SELECT count(*) FROM mineral_site) AS sites,
    (SELECT count(*) FROM sample) AS samples;
