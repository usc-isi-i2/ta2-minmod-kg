-- sites a GeoChem wipe would destroy that the loader did not create
SELECT count(*) FROM mineral_site WHERE created_by NOT LIKE '%/geochem-hmi';
