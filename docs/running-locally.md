# Running the local stack (no Docker)

Bring up Postgres, the API, the editor, and (optionally) the GeoChem HMI backend natively on your own machine, so you can test anything that reads or writes through the real API against real data. Once it's up, the last section walks through a few concrete things worth verifying — the GeoChem edit spec (`/papers/publish`) is one of several, not the only reason to have this running.

Assumes `ta2-minmod-kg` and `ta2-minmod-editor` are checked out as siblings:

```
<some-dir>/
├── ta2-minmod-kg
└── ta2-minmod-editor
```

## 1. Postgres

```bash
brew install postgresql@17
brew services start postgresql@17
psql postgres -c "CREATE ROLE minmod WITH LOGIN PASSWORD 'criticalmaas2025';"
psql postgres -c "CREATE DATABASE minmod OWNER minmod;"
```

Restore a real dump if you have one (`pg_restore -U minmod -d minmod -h localhost --no-owner --no-privileges -j 4 /path/to/dump`), or start empty — the API creates all tables on first boot either way. An empty instance has no mineral sites to attach samples to, so you'll want at least one real `site_id` in the `mineral_site` table before trying the GeoChem examples in §6.

## 2. API

```bash
cd ta2-minmod-kg
git submodule update --init   # GeoChem ontology/shapes, used for SHACL validation
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

## 3. Test user

```bash
python -m minmodkg.api user -u testuser -n "Test User" -e testuser@example.com --password 'Test1234!'

curl -s -c /tmp/cookies.txt -X POST http://localhost:8000/api/v1/login \
  -H "Content-Type: application/json" \
  -d '{"username":"testuser","password":"Test1234!"}'
```

## 4. Editor

```bash
cd ta2-minmod-editor/www
npm install
npm start
```

Create React App's dev server on `:3000`, proxying `/api/*` straight to `http://localhost:8000` (`"proxy"` in `package.json` — change that value, not an env var, if your API is on a different port). Open `http://localhost:3000`, log in as `testuser`/`Test1234!`. You'll see the real editor UI against real data (if you restored a dump). What you won't see: the `/geochem` tab (it's `/geochem/`-routed via nginx in the real deployment, not running here) or anything under `/dashboard`.

## 5. GeoChem HMI backend (optional)

To exercise the real GeoChem HMI backend against this API instead of curl, `geochem-hmi` has its own native, no-Docker harness — see that repo's `deploy/minmod-embed/LOCAL-HARNESS.md`, section "Sample publish (issue #18) without the full stack", and `hmi/backend/tests/test_minmod_live_publish.py`. It can either point at the API you just started in §2, or spawn its own throwaway SQLite-backed instance from this repo's venv (`MINMOD_KG_ROOT` env var) — the test file does the latter, so nothing from §1-2 needs to be running just to run that test.

## 6. Things to verify once it's up

A few concrete checks, roughly in order of how much of the stack they touch.

### Basic mineral-site read/write

```bash
SITE_ID=$(psql -U minmod -d minmod -h localhost -t -c "SELECT site_id FROM mineral_site LIMIT 1;" | xargs)
curl -s -b /tmp/cookies.txt "http://localhost:8000/api/v1/mineral-sites/$SITE_ID"
```
Or just browse to it in the editor UI (§4) and make an edit there.

### GeoChem edit spec — `POST /api/v1/papers/publish`

The sparse sample/analysis/element publish contract, including soft delete, Sample `location`, `strat_unit_name`, bare unit labels, and SHACL validation.

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

`grade_unit` here is a bare label (`"g/t"`), not the older wrapped `{observed_name, confidence, source, normalized_uri}` object — resolved server-side against known units by URI, then by name/alias, case-insensitively. Both shapes are accepted; the wrapped one is still what `GET` responses return. `errors` should be empty; `created`/`updated` should list the sample with `changed_properties` including `strat_unit_name` and `mo:location_info`'s real predicate URI.

Fetch it back:

```bash
PUBLIC_ID=$(python3 -c "
from minmodkg.transformations import make_sample_id
print(make_sample_id('$SITE_ID', 'SM-001'))
")
curl -s -b /tmp/cookies.txt "http://localhost:8000/api/v1/samples/$PUBLIC_ID"
```

**Soft delete:**

```bash
curl -s -b /tmp/cookies.txt -X POST http://localhost:8000/api/v1/papers/publish \
  -H "Content-Type: application/json" -d "{
  \"paper_id\": \"local-test-paper\",
  \"deposits\": [{\"mineral_site_id\": \"$SITE_ID\", \"samples\": [{\"sample_id\": \"SM-001\", \"is_deleted\": true}]}]
}"
```

`GET` the sample again — `is_deleted: true`, `deleted_by`/`deleted_at` stamped from the session (never send these yourself, they're rejected/ignored). Send `"is_deleted": false` the same way to undelete; both clear back to absent. Analysis/element-level delete works the same way, nested under `analyses[].elements[]`. Deposit-level (`mo:MineralSite`) delete is a different call — `PUT /api/v1/mineral-sites/{site_id}` with `is_deleted: true` in the body.

**SHACL validation** runs on the fully-formed post-edit sample before anything persists. Only rules marked a hard `sh:Violation` block; "recommended" (`sh:Warning`) rules never appear in `errors`. The normal API surface is typed, so the easiest way to see a real violation fire is the test suite:

```bash
pytest tests/test_validators.py -k TestValidateSampleShacl -v
pytest tests/services/test_sample.py -k TestSHACLValidation -v
```

## What this leaves out

- **nginx** — no `/geochem` routing, no `/dashboard`, no SPARQL routing through it.
- **Fuseki / triple-store sync** — writes land in Postgres only, nothing propagates to RDF or a JSON/git backup.
- **The real GeoChem HMI frontend UI** — §5's harness covers backend-to-backend publish, not clicking through the actual curation UI. That still needs either the deployed HMI (session-cookie-gated against real `minmod.isi.edu`) or building and serving the HMI's own frontend per its own docs.

## Stopping

`Ctrl-C` the API and `npm start`. Postgres keeps running as a background service — `brew services stop postgresql@17` when done, or leave it for next time.
