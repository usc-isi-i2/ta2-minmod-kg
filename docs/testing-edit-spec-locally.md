# Testing the GeoChem edit spec locally (no Docker)

Everything needed to exercise `POST /api/v1/papers/publish` (the sparse sample/analysis/element publish contract) against a real Postgres-backed API on your own machine — soft delete, Sample `location`, `strat_unit_name`, bare unit labels, and SHACL validation included. No Fuseki, no nginx, no HMI frontend.

Assumes `ta2-minmod-kg` is checked out and you're on a branch that has the edit-spec work (`geochem/sample-backend` as of this writing).

## 1. Postgres

```bash
brew install postgresql@17
brew services start postgresql@17
psql postgres -c "CREATE ROLE minmod WITH LOGIN PASSWORD 'criticalmaas2025';"
psql postgres -c "CREATE DATABASE minmod OWNER minmod;"
```

Restore a real dump if you have one (`pg_restore -U minmod -d minmod -h localhost --no-owner --no-privileges -j 4 /path/to/dump`), or start empty — the API creates all tables on first boot either way. An empty instance has no mineral sites to attach samples to, though, so you'll want at least one real `site_id` in the `mineral_site` table for step 3, from a restored dump or however you'd otherwise get one.

## 2. API

```bash
cd ta2-minmod-kg
python3.11 -m venv .venv
source .venv/bin/activate
poetry install --only main

cp config.yml.template /tmp/native-config.yml
sed -i '' "s|kgrel:.*|kgrel: postgresql+psycopg://minmod:criticalmaas2025@localhost:5432/minmod|" /tmp/native-config.yml

export CFG_FILE=/tmp/native-config.yml
fastapi run minmodkg/api/main.py --port 8000
```

Leave `triplestore` pointing at `http://kg:3030/...` in the config — nothing connects to it eagerly, harmless with no Fuseki running. `gco`/`gcr` namespaces are already in `config.yml.template`; if you're working from an older copy that's missing them, add:

```yaml
namespace:
  gco: https://geochemistry.isi.edu/ontology/
  gcr: https://geochemistry.isi.edu/resource/
```

Check it's up:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/v1/docs   # 200
```

`/tmp/native-config.yml` does not survive a reboot — recreate it before restarting the API after one.

## 3. Test user + a site to attach samples to

```bash
python -m minmodkg.api user -u testuser -n "Test User" -e testuser@example.com --password 'Test1234!'

SITE_ID=$(psql -U minmod -d minmod -h localhost -t -c "SELECT site_id FROM mineral_site LIMIT 1;" | xargs)

curl -s -c /tmp/cookies.txt -X POST http://localhost:8000/api/v1/login \
  -H "Content-Type: application/json" \
  -d '{"username":"testuser","password":"Test1234!"}'
```

## 4. Publish a sample with the newer fields

```bash
curl -s -b /tmp/cookies.txt -X POST http://localhost:8000/api/v1/papers/publish \
  -H "Content-Type: application/json" -d "{
  \"paper_id\": \"local-test-paper\",
  \"deposits\": [{
    \"mineral_site_id\": \"$SITE_ID\",
    \"samples\": [{
      \"sample_id\": \"SM-001\",
      \"sample_name\": \"local dev test\",
      \"strat_unit_name\": \"Fort Payne Formation\",
      \"location\": {\"coordinates\": \"POINT(-84.6 35.6)\"},
      \"analyses\": [{
        \"analysis_id\": \"A-1\",
        \"elements\": [{\"symbol\": \"Au\", \"grade\": 2.5, \"grade_unit\": \"g/t\"}]
      }]
    }]
  }]
}"
```

`grade_unit` here is a bare label (`"g/t"`), not the older wrapped `{observed_name, confidence, source, normalized_uri}` object — resolved server-side against known units by URI, then by name/alias, case-insensitively. Both shapes are accepted; the wrapped one is still what `GET` responses return.

`errors` should be empty and `created`/`updated` should list the sample with `changed_properties` including `strat_unit_name` and `mo:location_info`'s real predicate URI (not a guessed `gco:location`).

Fetch it back to see the round-trip, including SHACL-covered fields:

```bash
PUBLIC_ID=$(python3 -c "
from minmodkg.transformations import make_sample_id
print(make_sample_id('$SITE_ID', 'SM-001'))
")
curl -s -b /tmp/cookies.txt "http://localhost:8000/api/v1/samples/$PUBLIC_ID"
```

## 5. Soft delete

```bash
curl -s -b /tmp/cookies.txt -X POST http://localhost:8000/api/v1/papers/publish \
  -H "Content-Type: application/json" -d "{
  \"paper_id\": \"local-test-paper\",
  \"deposits\": [{\"mineral_site_id\": \"$SITE_ID\", \"samples\": [{\"sample_id\": \"SM-001\", \"is_deleted\": true}]}]
}"
```

`GET` the sample again — `is_deleted: true`, `deleted_by`/`deleted_at` stamped from the session (never send these yourself, they're rejected/ignored). Send `"is_deleted": false` the same way to undelete; both clear back to absent. Analysis/element-level delete works the same way, nested under `analyses[].elements[]`. Deposit-level (`mo:MineralSite`) delete is a different call — `PUT /api/v1/mineral-sites/{site_id}` with `is_deleted: true` in the body, not this endpoint.

## 6. A SHACL failure

Real SHACL validation runs on the fully-formed post-edit sample before anything persists (both on create and on patch). Only rules the shape file marks as a hard `sh:Violation` actually block — "recommended" (`sh:Warning`) rules never appear in `errors`. To see one fire, send something the shape genuinely rejects, e.g. a non-boolean `is_deleted` won't happen through the normal API surface (it's typed), so the practical way to see this path exercised is the existing test suite:

```bash
pytest tests/test_validators.py -k TestValidateSampleShacl -v
pytest tests/services/test_sample.py -k TestSHACLValidation -v
```

## 7. Testing with the real HMI backend in the loop

If you want to go further than curl — the real GeoChem HMI backend, talking to a real (SQLite-backed) `SampleService.publish()`, no Docker — `geochem-hmi`'s own local test harness does this. See that repo's `deploy/minmod-embed/LOCAL-HARNESS.md`, section "Sample publish (issue #18) without the full stack", and `hmi/backend/tests/test_minmod_live_publish.py`. It spawns `ta2-minmod-kg`'s API as a subprocess from this repo's own venv (`MINMOD_KG_ROOT` env var points at the checkout), so nothing here needs to be running for that to work — it starts its own instance.

## What this leaves out

- **nginx** — no `/geochem` routing, no `/dashboard`, no SPARQL routing through it.
- **Fuseki / triple-store sync** — writes land in Postgres only, nothing propagates to RDF or a JSON/git backup.
- **The real GeoChem HMI frontend UI** — §7's harness covers backend-to-backend publish, not clicking through the actual curation UI. That still needs either the deployed HMI (session-cookie-gated against real `minmod.isi.edu`) or building and serving the HMI's own frontend per its own docs.

## Stopping

`Ctrl-C` the API. Postgres keeps running as a background service — `brew services stop postgresql@17` when done, or leave it for next time.
