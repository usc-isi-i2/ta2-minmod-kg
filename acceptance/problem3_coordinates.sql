-- Problem 3 acceptance: no (0, 0) points, and the precision tie-break.
-- Read-only: plain SELECTs. Run against the kgrel Postgres, once before the
-- deploy and once after the ETL has rebuilt the merged tables:
--
--   PGOPTIONS='-c default_transaction_read_only=on' psql "$DATABASE_URL" \
--     -f acceptance/problem3_coordinates.sql
--
-- One row per metric: metric, value, expected. `expected` is the value after
-- the deploy, `before` the value before it, both measured on a local load of
-- ta2-minmod-data 0c8b827. Newer data can drift a little; the 0 rows should
-- stay 0 and the "unchanged" rows should equal the before run.
--
-- If the statickg workdir's merge cache is not merge-v110 (or later)
-- (services/mineralsiteetl/merge-v*.sqlite), the merge did not re-run and
-- nothing here will have changed. With #110 (state repair) in the same build,
-- "merged sites with a state" also drops by 228; no other row depends on it.
--
-- SQL, not SPARQL: lat/lon exist only in Postgres
-- (mineral_site.location_view, dedup_mineral_site.coordinates, bytea holding
-- orjson). The source WKT is kept as written, so mineral_site.location and the
-- KG still say POINT (0 0) for those records; only their lat/lon is dropped.
--
-- The listed sites are the 197 merged sites that change in an offline rebuild
-- of that data: 29 that had a (0, 0) point, and 168 whose pick moves to a tied
-- record that refines the same point. A site "has the expected point" when its
-- coordinates come from the listed record (coordinates.refid).

WITH
listed (kind, dedup_site_id, site_id) AS (
    VALUES
    ('null island: no point', 'dedup_site__api-cdr-land-v1-docs-documents__02056bff0e49090c45a110521a11aa2d03ff1461420d315fd1cd58a88ad6c0fc98__inferlink', NULL),
    ('null island: no point', 'dedup_site__api-cdr-land-v1-docs-documents__020d3f906a634e9526c27a5e34d4eb042fccd394a3a23401e640f1b65657e5d958__inferlink', NULL),
    ('null island: no point', 'dedup_site__api-cdr-land-v1-docs-documents__02127ab53e01dbd78c1f2f8d0e72e53382fa73fe92f64519863e0aed1d62ad7ef2__inferlink', NULL),
    ('null island: no point', 'dedup_site__api-cdr-land-v1-docs-documents__0215aad4ec1402eb5388b75bc65d5905524a6a4a999e16ec8496fb7a8b8c6c3c96__inferlink', NULL),
    ('null island: no point', 'dedup_site__api-cdr-land-v1-docs-documents__0226cb80ce39e7a4976d0ceeaf3afbd324ea7b808f365c7477474cb885d78bfdc6__inferlink', NULL),
    ('null island: no point', 'dedup_site__api-cdr-land-v1-docs-documents__0231f8c81ba8440db92cded3e73732613be96fcfd716b7ce53d9a58922cc9e0714__inferlink', NULL),
    ('null island: no point', 'dedup_site__api-cdr-land-v1-docs-documents__0232adc7d9164e58f56de673680c8d1b5cb207c6397dfca0bd18e48478d590b8b4__inferlink', NULL),
    ('null island: no point', 'dedup_site__api-cdr-land-v1-docs-documents__0256ca3ba78f0ec6b77d2d4d4973d2da775cee7a534dfc71f56ee416cb5c437bec__inferlink', NULL),
    ('null island: no point', 'dedup_site__api-cdr-land-v1-docs-documents__025dad46c2d1ac501f013c416dadae9d91684a8c129f928657a780b394435450dc__inferlink', NULL),
    ('null island: no point', 'dedup_site__api-cdr-land-v1-docs-documents__025e71162fd48b8067eb33f63d83679ae4b564b63e1bd27441bfc2c88d01511ddc__inferlink', NULL),
    ('null island: no point', 'dedup_site__api-cdr-land-v1-docs-documents__026059eb3cc3b41e748b34afaebe846b9374593c53661085b8bd5604f931175af4__inferlink', NULL),
    ('null island: no point', 'dedup_site__api-cdr-land-v1-docs-documents__026351c77e3027b3db9d4290e3b7e4f5b7062db40dae5cdf320ea726fa5e59123d__inferlink', NULL),
    ('null island: no point', 'dedup_site__api-cdr-land-v1-docs-documents__026562095c61b0e69e3d97069935a1348db770e5035900887176bc2b3298964b3e__inferlink', NULL),
    ('null island: no point', 'dedup_site__api-cdr-land-v1-docs-documents__026b22dd9cce535c80d578f5598ade2801434de617f98c423706b74b21b3ae0842__inferlink', NULL),
    ('null island: no point', 'dedup_site__api-cdr-land-v1-docs-documents__0274452b874b201ce3aa34f220fcb9aa6a0c15d90c7f2c9cf39f700d5dd9cd59fc__inferlink', NULL),
    ('null island: no point', 'dedup_site__api-cdr-land-v1-docs-documents__029aae55599abf47ce32031ffa6dc594c63936ebe582cfc3104d67ba69aa85658b__inferlink', NULL),
    ('null island: no point', 'dedup_site__api-cdr-land-v1-docs-documents__02b51467dc99411ba9a24d2c5c9ba307310bae6c04576019098ea9676a825af089__inferlink', NULL),
    ('null island: no point', 'dedup_site__api-cdr-land-v1-docs-documents__02b5dafea360e36c4b96581d28a659da4eec1d56aa817a4a7e20374e8455201534__inferlink', NULL),
    ('null island: no point', 'dedup_site__api-cdr-land-v1-docs-documents__02c01eeed442b31fe61b70d39ec01a77052798f7aecc2d9d401da47d1e3c3ff075__inferlink', NULL),
    ('null island: no point', 'dedup_site__api-cdr-land-v1-docs-documents__02c1e1ca772fe97dfc2d3a8923ccccd3a090b79c3c74a92e6f1ff093b08cbf0a57__inferlink', NULL),
    ('null island: no point', 'dedup_site__api-cdr-land-v1-docs-documents__02da8f2d18a0c8cc6ad9285bef270bddf4148b06770e1f8715587151aab9014601__inferlink', NULL),
    ('null island: no point', 'dedup_site__api-cdr-land-v1-docs-documents__02dd6a62cc34af285bacd9155ce898b56b1338bbf12797420a5e59ec82b6bab849__inferlink', NULL),
    ('null island: no point', 'dedup_site__api-cdr-land-v1-docs-documents__02e5f5c32040e7d906e2bb85e29e4956bbe7a0b531c4ec98f559e855e256f65616__inferlink', NULL),
    ('null island: no point', 'dedup_site__api-cdr-land-v1-docs-documents__02e7ff83ac75a4749dcd3be7c2987b1402d9dc7f7f38b56299212c4f59a8cb4d64__inferlink', NULL),
    ('null island: no point', 'dedup_site__api-cdr-land-v1-docs-documents__02ee2d7c9e7a88c26e2fb1bd42bb4bbd845da365fced615ad9f83e9fe39ed0f845__inferlink', NULL),
    ('null island: fallback', 'dedup_site__api-cdr-land-v1-docs-documents__021c1446132f9a25c69d97437586151f0700b11bedb2dc2146065224353baeb19f__inferlink', 'site__api-cdr-land-v1-docs-documents__02bb6d116d8846748f146bcf00145f1a05bf8f7b3435a5f928e1178a7ed5b78ac9__inferlink'),
    ('null island: fallback', 'dedup_site__api-cdr-land-v1-docs-documents__022513c39fa4e92fb35d33a742eed6dda037bd96ca815470c3e2bd52132633bf30__inferlink', 'site__api-cdr-land-v1-docs-documents__02c1b00d631c8993f206677024cd9e47c438f3d15ba31253a96f79f423b732de20__inferlink'),
    ('null island: fallback', 'dedup_site__api-cdr-land-v1-docs-documents__022da331592e472e5ccaadf09abc517789a6a3fd97b60f04efa335c841db192571__inferlink', 'site__api-cdr-land-v1-docs-documents__0280c84bf0827121919b1f5801e6a61f290cffbeada88ffd6ed0bb45584533bcbb__inferlink'),
    ('null island: fallback', 'dedup_site__api-cdr-land-v1-docs-documents__0254ffd63b70e870b16fd718a001cb736b9025e3df1575b3b9aedf9c13987dfde2__inferlink', 'site__mrdata-usgs-gov-mrds__10231516__umn'),
    ('tie-break', 'dedup_site__api-cdr-land-v1-docs-documents__02282971b6ef3459b289f53b69054519af9a099a7590ec1178cedd4bd35aa7b9f8__inferlink', 'site__api-cdr-land-v1-docs-documents__02282971b6ef3459b289f53b69054519af9a099a7590ec1178cedd4bd35aa7b9f8__inferlink'),
    ('tie-break', 'dedup_site__doi-org-10-1016-j-oregeorev-2016-08-010__gayna-river-240__usc', 'site__mrdata-usgs-gov-sedexmvt__90__umn'),
    ('tie-break', 'dedup_site__doi-org-10-2113-econgeo-110-8-1925__kangankunde-175__usc', 'site__mrdata-usgs-gov-carbonatite__36__umn'),
    ('tie-break', 'dedup_site__doi-org-10-2113-econgeo-110-8-1925__mountain-pass-253__usc', 'site__doi-org-10-5066-p9k1hbnt__421__umn'),
    ('tie-break', 'dedup_site__doi-org-10-2113-econgeo-110-8-1925__olympic-dam-109__usc', 'site__mrdata-usgs-gov-major-deposits__30__umn'),
    ('tie-break', 'dedup_site__doi-org-10-34191-ofr-757__4582__umn', 'site__doi-org-10-34191-ofr-757__4582__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5066-p96mmrfd__ch065__sri', 'site__doi-org-10-5066-p96mmrfd__ch065__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5066-p96mmrfd__tl002__sri', 'site__doi-org-10-5066-p96mmrfd__tl003__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5066-p96mmrfd__tn093__sri', 'site__doi-org-10-5066-p96mmrfd__tn093__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5066-p9htergk__10711__umn', 'site__doi-org-10-5066-p9htergk__10711__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5066-p9htergk__10726__umn', 'site__doi-org-10-5066-p9htergk__10726__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5066-p9htergk__11008__umn', 'site__doi-org-10-5066-p9htergk__11008__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5066-p9htergk__11023__umn', 'site__doi-org-10-5066-p9htergk__11023__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5066-p9htergk__14758__umn', 'site__doi-org-10-5066-p9htergk__14758__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5066-p9htergk__16474__umn', 'site__doi-org-10-5066-p9htergk__16474__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5066-p9htergk__18748__umn', 'site__doi-org-10-5066-p9htergk__18748__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5066-p9htergk__19085__umn', 'site__doi-org-10-5066-p9htergk__19085__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5066-p9htergk__29792__umn', 'site__doi-org-10-5066-p9htergk__29792__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5066-p9htergk__36658__umn', 'site__doi-org-10-5066-p9htergk__36658__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5066-p9htergk__37395__umn', 'site__doi-org-10-5066-p9htergk__37395__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5066-p9htergk__37588__umn', 'site__doi-org-10-5066-p9htergk__37588__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5066-p9htergk__39171__umn', 'site__doi-org-10-5066-p9htergk__39194__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5066-p9k1hbnt__104__umn', 'site__doi-org-10-5066-p9k1hbnt__104__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5066-p9k1hbnt__122__umn', 'site__doi-org-10-5066-p9k1hbnt__122__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5066-p9k1hbnt__231__umn', 'site__doi-org-10-5066-p9k1hbnt__231__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5066-p9k1hbnt__275__umn', 'site__doi-org-10-5066-p9k1hbnt__275__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5066-p9k1hbnt__294__umn', 'site__doi-org-10-5066-p9k1hbnt__294__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5066-p9k1hbnt__411__umn', 'site__doi-org-10-5066-p9k1hbnt__411__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5066-p9k1hbnt__438__umn', 'site__doi-org-10-5066-p9k1hbnt__438__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5066-p9k1hbnt__445__umn', 'site__mrdata-usgs-gov-pp1802__1645__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5066-p9k1hbnt__505__umn', 'site__doi-org-10-5066-p9k1hbnt__505__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5066-p9k1hbnt__566__umn', 'site__doi-org-10-5066-p9k1hbnt__566__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5066-p9k1hbnt__622__umn', 'site__doi-org-10-5066-p9k1hbnt__622__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5382-econgeo-2018-4590__chaucha-1015__usc', 'site__mrdata-usgs-gov-major-deposits__1157__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5382-econgeo-2018-4590__el-arco-1314__usc', 'site__mrdata-usgs-gov-major-deposits__1187__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5382-econgeo-2018-4590__el-galeno-1520__usc', 'site__mrdata-usgs-gov-major-deposits__1266__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5382-econgeo-2018-4590__klein-aub-1398__usc', 'site__mrdata-usgs-gov-sedcu__93__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5382-econgeo-2018-4590__los-pinos-1534__usc', 'site__mrdata-usgs-gov-major-deposits__1269__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5382-econgeo-2018-4590__malanjkhand-1160__usc', 'site__mrdata-usgs-gov-major-deposits__256__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5382-econgeo-2018-4590__san-carlos-1028__usc', 'site__mrdata-usgs-gov-major-deposits__1161__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5382-econgeo-2018-4590__san-fabian-1603__usc', 'site__mrdata-usgs-gov-major-deposits__426__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5382-econgeo-2018-4590__san-jorge-53__usc', 'site__mrdata-usgs-gov-major-deposits__878__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5382-econgeo-2018-4590__tsagaan-suvarga-1373__usc', 'site__mrdata-usgs-gov-major-deposits__347__umn'),
    ('tie-break', 'dedup_site__doi-org-10-5382-econgeo-2018-4590__yandera-1490__usc', 'site__mrdata-usgs-gov-major-deposits__422__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-carbonatite__21__sri', 'site__mrdata-usgs-gov-carbonatite__21__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-carbonatite__26__sri', 'site__mrdata-usgs-gov-carbonatite__26__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-carbonatite__34__sri', 'site__mrdata-usgs-gov-carbonatite__34__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-carbonatite__35__sri', 'site__mrdata-usgs-gov-carbonatite__35__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-carbonatite__37__sri', 'site__mrdata-usgs-gov-carbonatite__37__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-carbonatite__3__sri', 'site__mrdata-usgs-gov-carbonatite__3__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-carbonatite__7__sri', 'site__mrdata-usgs-gov-carbonatite__7__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-major-deposits__1096__sri', 'site__mrdata-usgs-gov-major-deposits__1096__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-major-deposits__1099__sri', 'site__mrdata-usgs-gov-major-deposits__1099__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-major-deposits__1108__sri', 'site__mrdata-usgs-gov-major-deposits__1108__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-major-deposits__1116__sri', 'site__mrdata-usgs-gov-major-deposits__1116__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-major-deposits__1120__sri', 'site__mrdata-usgs-gov-major-deposits__1120__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-major-deposits__1134__sri', 'site__mrdata-usgs-gov-major-deposits__1134__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-major-deposits__114__sri', 'site__mrdata-usgs-gov-major-deposits__114__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-major-deposits__1158__sri', 'site__mrdata-usgs-gov-major-deposits__1158__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-major-deposits__1180__sri', 'site__mrdata-usgs-gov-major-deposits__1180__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-major-deposits__1255__sri', 'site__mrdata-usgs-gov-major-deposits__1255__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-major-deposits__1268__sri', 'site__mrdata-usgs-gov-major-deposits__1268__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-major-deposits__1281__sri', 'site__mrdata-usgs-gov-major-deposits__1281__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-major-deposits__199__umn', 'site__mrdata-usgs-gov-major-deposits__199__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-major-deposits__2412__sri', 'site__mrdata-usgs-gov-major-deposits__2412__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-major-deposits__283__sri', 'site__mrdata-usgs-gov-major-deposits__283__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-major-deposits__285__sri', 'site__mrdata-usgs-gov-major-deposits__285__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-major-deposits__28__sri', 'site__mrdata-usgs-gov-major-deposits__28__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-major-deposits__334__umn', 'site__mrdata-usgs-gov-major-deposits__334__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-major-deposits__344__sri', 'site__mrdata-usgs-gov-major-deposits__344__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-major-deposits__428__sri', 'site__mrdata-usgs-gov-major-deposits__428__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-major-deposits__430__sri', 'site__mrdata-usgs-gov-major-deposits__430__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-major-deposits__431__umn', 'site__mrdata-usgs-gov-major-deposits__431__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-major-deposits__433__umn', 'site__mrdata-usgs-gov-major-deposits__433__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-major-deposits__436__umn', 'site__mrdata-usgs-gov-major-deposits__436__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-major-deposits__487__sri', 'site__mrdata-usgs-gov-major-deposits__487__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-major-deposits__488__sri', 'site__mrdata-usgs-gov-major-deposits__488__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-major-deposits__58__sri', 'site__mrdata-usgs-gov-major-deposits__58__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-major-deposits__640__sri', 'site__mrdata-usgs-gov-major-deposits__640__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-major-deposits__829__sri', 'site__mrdata-usgs-gov-pp1802__1644__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-major-deposits__854__sri', 'site__mrdata-usgs-gov-major-deposits__854__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-major-deposits__897__sri', 'site__mrdata-usgs-gov-major-deposits__897__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-major-deposits__921__sri', 'site__mrdata-usgs-gov-major-deposits__921__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-major-deposits__964__sri', 'site__mrdata-usgs-gov-major-deposits__964__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-major-deposits__977__umn', 'site__mrdata-usgs-gov-major-deposits__977__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10010404__umn', 'site__mrdata-usgs-gov-mrds__10010404__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10010744__umn', 'site__mrdata-usgs-gov-mrds__10010744__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10010850__sri', 'site__mrdata-usgs-gov-mrds__10010850__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10013142__sri', 'site__mrdata-usgs-gov-mrds__10013142__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10014044__sri', 'site__mrdata-usgs-gov-mrds__10014044__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10015890__umn', 'site__mrdata-usgs-gov-mrds__10015890__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10015898__sri', 'site__mrdata-usgs-gov-mrds__10015898__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10017371__sri', 'site__mrdata-usgs-gov-mrds__10017371__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10017773__sri', 'site__mrdata-usgs-gov-mrds__10017773__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10019651__sri', 'site__mrdata-usgs-gov-mrds__10019651__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10020044__sri', 'site__mrdata-usgs-gov-mrds__10020044__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10020915__umn', 'site__mrdata-usgs-gov-mrds__10020915__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10021239__sri', 'site__mrdata-usgs-gov-mrds__10021239__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10024977__sri', 'site__mrdata-usgs-gov-pp1802__1642__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10025092__sri', 'site__mrdata-usgs-gov-mrds__10025092__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10026455__sri', 'site__mrdata-usgs-gov-mrds__10026455__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10029540__umn', 'site__mrdata-usgs-gov-mrds__10029540__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10033274__sri', 'site__mrdata-usgs-gov-mrds__10033274__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10033276__sri', 'site__mrdata-usgs-gov-mrds__10033276__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10033958__umn', 'site__mrdata-usgs-gov-mrds__10033958__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10033962__sri', 'site__mrdata-usgs-gov-mrds__10033962__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10034176__umn', 'site__mrdata-usgs-gov-mrds__10034176__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10036866__sri', 'site__mrdata-usgs-gov-mrds__10056059__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10042543__sri', 'site__mrdata-usgs-gov-mrds__10042543__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10042718__sri', 'site__www-oregongeology-org__id-09872__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10043691__sri', 'site__mrdata-usgs-gov-mrds__10043691__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10043943__sri', 'site__mrdata-usgs-gov-mrds__10043943__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10044706__sri', 'site__mrdata-usgs-gov-mrds__10044706__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10047406__sri', 'site__mrdata-usgs-gov-mrds__10047406__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10048303__sri', 'site__mrdata-usgs-gov-mrds__10048303__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10048618__sri', 'site__mrdata-usgs-gov-mrds__10055920__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10048879__sri', 'site__mrdata-usgs-gov-mrds__10048879__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10048892__sri', 'site__mrdata-usgs-gov-mrds__10048892__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10049068__sri', 'site__mrdata-usgs-gov-mrds__10049068__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10049096__sri', 'site__mrdata-usgs-gov-mrds__10049096__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10049365__sri', 'site__mrdata-usgs-gov-mrds__10049365__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10049553__sri', 'site__mrdata-usgs-gov-mrds__10049553__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10049576__umn', 'site__mrdata-usgs-gov-mrds__10049576__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10049916__sri', 'site__mrdata-usgs-gov-mrds__10049916__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10049950__sri', 'site__mrdata-usgs-gov-mrds__10049950__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10050180__umn', 'site__mrdata-usgs-gov-mrds__10050180__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10050748__sri', 'site__mrdata-usgs-gov-mrds__10050748__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10052523__sri', 'site__mrdata-usgs-gov-mrds__10052523__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10054258__sri', 'site__mrdata-usgs-gov-mrds__10054258__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10054261__sri', 'site__mrdata-usgs-gov-mrds__10054261__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10067868__sri', 'site__mrdata-usgs-gov-mrds__10067868__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10068850__sri', 'site__mrdata-usgs-gov-mrds__10068850__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10070635__sri', 'site__mrdata-usgs-gov-mrds__10070635__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10077905__umn', 'site__mrdata-usgs-gov-mrds__10077905__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10078754__sri', 'site__mrdata-usgs-gov-mrds__10078754__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10078780__sri', 'site__mrdata-usgs-gov-mrds__10078780__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10078806__sri', 'site__mrdata-usgs-gov-mrds__10078806__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10078823__sri', 'site__mrdata-usgs-gov-mrds__10078823__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10082605__sri', 'site__mrdata-usgs-gov-mrds__10082605__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10082844__sri', 'site__mrdata-usgs-gov-mrds__10082844__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10082850__sri', 'site__mrdata-usgs-gov-mrds__10082850__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10087079__sri', 'site__mrdata-usgs-gov-mrds__10087079__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10088187__sri', 'site__mrdata-usgs-gov-mrds__10088187__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10088190__umn', 'site__mrdata-usgs-gov-mrds__10088190__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10088982__umn', 'site__mrdata-usgs-gov-mrds__10094844__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10089480__sri', 'site__mrdata-usgs-gov-mrds__10089480__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10089566__sri', 'site__mrdata-usgs-gov-mrds__10089566__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10089569__sri', 'site__mrdata-usgs-gov-mrds__10089569__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10089672__sri', 'site__mrdata-usgs-gov-mrds__10089672__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10091231__sri', 'site__mrdata-usgs-gov-mrds__10091231__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10093059__umn', 'site__mrdata-usgs-gov-mrds__10093059__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10093064__umn', 'site__mrdata-usgs-gov-mrds__10093064__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10094997__sri', 'site__mrdata-usgs-gov-mrds__10094997__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10096665__umn', 'site__mrdata-usgs-gov-mrds__10096665__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10097795__sri', 'site__mrdata-usgs-gov-mrds__10097795__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10097949__sri', 'site__mrdata-usgs-gov-mrds__10097949__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10098721__sri', 'site__mrdata-usgs-gov-mrds__10098721__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10100205__sri', 'site__mrdata-usgs-gov-mrds__10100205__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10100311__sri', 'site__mrdata-usgs-gov-mrds__10100311__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10107352__sri', 'site__mrdata-usgs-gov-mrds__10107352__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10107371__sri', 'site__mrdata-usgs-gov-mrds__10107371__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-mrds__10111208__sri', 'site__mrdata-usgs-gov-mrds__10111208__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-pp1802__768__umn', 'site__mrdata-usgs-gov-pp1802__768__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-sedcu__523__sri', 'site__mrdata-usgs-gov-sedcu__523__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-sedcu__626__sri', 'site__mrdata-usgs-gov-sedcu__626__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-sedcu__629__sri', 'site__mrdata-usgs-gov-sedcu__629__umn'),
    ('tie-break', 'dedup_site__mrdata-usgs-gov-sedexmvt__37__sri', 'site__mrdata-usgs-gov-sedexmvt__37__umn'),
    ('tie-break', 'dedup_site__www-gsa-state-al-us-gsa-geologic-nonfuelminerals-nfcritresource__542__umn', 'site__www-gsa-state-al-us-gsa-geologic-nonfuelminerals-nfcritresource__545__umn')
),

listed_now AS (
    SELECT
        l.kind,
        l.site_id,
        d.id IS NOT NULL AS present,
        convert_from(d.coordinates, 'UTF8')::jsonb AS coord
    FROM listed AS l
    LEFT JOIN dedup_mineral_site AS d ON l.dedup_site_id = d.id
),

merged AS MATERIALIZED (
    SELECT convert_from(d.coordinates, 'UTF8')::jsonb -> 'value' AS v
    FROM dedup_mineral_site AS d
),

-- 6. the funnel from the Problem 3 review, rows 1-3: members of each merged
-- site with MinMod's own scores from ranked_sites; suborder[0] is true for
-- expert/curator records
member AS (
    SELECT
        d.id AS dedup_id,
        rs ->> 'site_id' AS site_id,
        (rs -> 'score' ->> 'score')::float8 AS score,
        (rs -> 'score' -> 'suborder' ->> 0)::boolean AS is_expert
    FROM dedup_mineral_site AS d
    CROSS JOIN
        LATERAL jsonb_array_elements(
            convert_from(d.ranked_sites, 'UTF8')::jsonb
        ) AS rs
),

m AS (
    SELECT
        member.*,
        ms.source_id,
        (v.lv ->> 'lat') IS NOT NULL AND (v.lv ->> 'lon') IS NOT NULL AS has_coord
    FROM member
    INNER JOIN mineral_site AS ms ON member.site_id = ms.site_id
    CROSS JOIN
        LATERAL (
            SELECT convert_from(ms.location_view, 'UTF8')::jsonb AS lv
        ) AS v
),

-- per merged site: members, members with a coordinate, top score among those
g AS (
    SELECT
        dedup_id,
        count(*) AS n_members,
        count(*) FILTER (WHERE has_coord) AS n_coord,
        max(score) FILTER (WHERE has_coord) AS top
    FROM m
    GROUP BY dedup_id
),

-- the members with a coordinate that hold that top score
t AS (
    SELECT
        g.dedup_id,
        count(*) AS n_top,
        count(DISTINCT m.source_id) AS n_top_sources,
        bool_or(m.is_expert) AS expert_top
    FROM g
    INNER JOIN m ON g.dedup_id = m.dedup_id AND m.has_coord AND g.top = m.score
    GROUP BY g.dedup_id
),

funnel AS (
    SELECT
        count(*) FILTER (WHERE g.n_members >= 2) AS row1,
        count(*) FILTER (WHERE g.n_coord >= 2) AS row2,
        count(*) FILTER (WHERE g.n_coord >= 2 AND t.expert_top) AS row2a,
        count(*) FILTER (
            WHERE g.n_coord >= 2 AND NOT t.expert_top AND t.n_top >= 2
        ) AS row3,
        count(*) FILTER (
            WHERE
                g.n_coord >= 2 AND NOT t.expert_top AND t.n_top >= 2
                AND t.n_top_sources = 1
        ) AS row3_same,
        count(*) FILTER (
            WHERE
                g.n_coord >= 2 AND NOT t.expert_top AND t.n_top >= 2
                AND t.n_top_sources >= 2
        ) AS row3_diff
    FROM g
    LEFT JOIN t ON g.dedup_id = t.dedup_id
),

metrics (sort, metric, value, expected) AS (
    -- 1. and 2. no (0, 0) point is left
    SELECT
        101,
        'merged sites whose point is (0, 0)',
        (
            SELECT count(*) FROM merged
            WHERE (v ->> 'lat')::float8 = 0 AND (v ->> 'lon')::float8 = 0
        ),
        '0 (before 29)'
    UNION ALL
    SELECT
        102,
        'raw sites whose lat/lon is (0, 0)',
        (
            SELECT count(*) FROM mineral_site AS ms
            CROSS JOIN
                LATERAL (
                    SELECT convert_from(ms.location_view, 'UTF8')::jsonb AS lv
                ) AS v
            WHERE
                (v.lv ->> 'lat')::float8 = 0 AND (v.lv ->> 'lon')::float8 = 0
        ),
        '0 (before 37)'

    UNION ALL

    -- 3. the 168 tie-break changes
    SELECT
        201,
        'tie-break (168 listed): on the expected more precise point',
        count(*) FILTER (
            WHERE kind = 'tie-break' AND coord ->> 'refid' = site_id
        ),
        '168 (before 0)'
    FROM listed_now

    UNION ALL

    -- 4. the 29 Null Island sites
    SELECT
        301,
        'Null Island (25 listed): now with no point',
        count(*) FILTER (
            WHERE kind = 'null island: no point' AND present AND coord IS NULL
        ),
        '25 (before 0)'
    FROM listed_now
    UNION ALL
    SELECT
        302,
        'Null Island (4 listed): now on another member''s point',
        count(*) FILTER (
            WHERE kind = 'null island: fallback' AND coord ->> 'refid' = site_id
        ),
        '4 (before 0)'
    FROM listed_now

    UNION ALL

    -- 5. sanity: must not move, except the merged sites with a coordinate
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
        'merged sites with a coordinate',
        (SELECT count(*) FROM merged WHERE v IS NOT NULL),
        '400,421 (before 400,446; the 25 Null Island sites lose theirs)'
    UNION ALL
    SELECT
        404,
        'merged sites with a country',
        (SELECT count(*) FROM dedup_mineral_site WHERE cardinality(country_val) > 0),
        'unchanged (402,429)'
    UNION ALL
    SELECT
        405,
        'merged sites with a state',
        (
            SELECT count(*) FROM dedup_mineral_site
            WHERE cardinality(state_or_province_val) > 0
        ),
        'unchanged (374,202); 228 lower if #110 is in the same build'

    UNION ALL

    SELECT
        501,
        'funnel 1: merged sites with >= 2 member records',
        row1,
        'unchanged (198,129)'
    FROM funnel
    UNION ALL
    SELECT
        502,
        'funnel 2: ... >= 2 members with a coordinate',
        row2,
        '23,281 (before 23,283)'
    FROM funnel
    UNION ALL
    SELECT
        503,
        'funnel 2a: ... top score held by an expert/curator',
        row2a,
        'unchanged (23)'
    FROM funnel
    UNION ALL
    SELECT
        504,
        'funnel 3: ... top score tied (system records)',
        row3,
        '18,580 (before 18,583)'
    FROM funnel
    UNION ALL
    SELECT
        505,
        'funnel 3: ... tied, same source',
        row3_same,
        '17,728 (before 17,730)'
    FROM funnel
    UNION ALL
    SELECT
        506,
        'funnel 3: ... tied, different sources',
        row3_diff,
        '852 (before 853)'
    FROM funnel
)

SELECT
    metric,
    value,
    expected
FROM metrics
ORDER BY sort;
