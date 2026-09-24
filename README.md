# Overview

The following repositories contain all of the code and data for building and running the KG:

- This repository for populating and publishing the knowledge graph (KG):
  https://github.com/DARPA-CRITICALMAAS/ta2-minmod-kg
- Repository for loading new databases and reconciling mining sites:
  https://github.com/DARPA-CRITICALMAAS/umn-ta2-mineral-site-linkage
- Repository for extracting data from mining reports:
  https://github.com/DARPA-CRITICALMAAS/ta2-extraction
- Repository for table understanding:
  https://github.com/DARPA-CRITICALMAAS/ta2-table-understanding
- Repository for the Deposit Type Classification:
  https://github.com/DARPA-CRITICALMAAS/sri-ta2
- Repository for the minmod editor:
  https://github.com/DARPA-CRITICALMAAS/ta2-minmod-editor
- Repository where each component publishes the data (we then use that data to automatically populate the KG):
  https://github.com/DARPA-CRITICALMAAS/ta2-minmod-data
- Repository for deployment of the data browser:
  https://github.com/DARPA-CRITICALMAAS/minmod-dashboard
- Live application that wraps the KG: https://minmod.isi.edu/

## TA2-MinMod-KG Repository Structure

- [containers](/containers): scripts to build docker images to deploy TA2 KG and other related services.
- [extractors](/extractors): [d-repr](https://github.com/usc-isi-i2/d-repr) models to convert [TA2 data](https://github.com/DARPA-CRITICALMAAS/ta2-minmod-data/) in to RDF to import into TA2 KG.
- [schema](/schema): the [ontology definition](/schema/ontology.ttl) and other generated files such as ER diagram and SHACL definition (for data validation)
- [tests](/tests): testing code
- [minmodkg](/minmodkg): main Python code

## Installation

### Setup the workspace

Setup the workspace by cloning [ta2-minmod-data](https://github.com/DARPA-CRITICALMAAS/ta2-minmod-data) and this repository [ta2-minmod-kg](/) inside your `WORKDIR`

```bash
git clone --depth 1 https://github.com/DARPA-CRITICALMAAS/ta2-minmod-data
git clone --depth 1 --recurse-submodules https://github.com/DARPA-CRITICALMAAS/ta2-minmod-kg
mkdir kgdata
mkdir config
```

`--recurse-submodules` is required: the GeoChem ontology and SHACL shapes come from the `ta2-table-understanding` submodule at `vendor/ta2-table-understanding`. Without it, GeoChem publishes fail SHACL validation and the backend Docker build fails. For an existing clone, run `git submodule update --init` once, then optionally `git config submodule.recurse true` so later pulls update the submodule automatically.

The directory will look like this

    <WORKDIR>
      ├── kgdata                    # for storing databases
      ├── config                    # for storing configuration
      ├── ta2-minmod-data           # ta2-minmod-data repository
      └── ta2-minmod-kg             # code to setup TA2 KG

### Setup the environment variables

The following commands will use these environment variables:

1. `USER_ID` & `GROUP_ID`: the current user id, this is to make sure the docker containers will create files with the same owner as the current user. You can set the `USER_ID` and `GROUP_ID` automatically using this command:

   ```
   export USER_ID=$(id -u)
   export GROUP_ID=$(id -g)
   ```

2. `CERT_DIR`: a directory containing SSL certificate (`fullchain.pem` and `privkey.pem`) for your server (see more at [Generating an SSL certificate](#generating-an-ssl-certificate)).

3. `CFG_DIR`: a directory containing necessary files for the API such as `config.yml`

To make it easy to set these environment variables, you can create a copied file named `.myenv` from [`env.template`](/env.template) and update the values accordingly. Then you can run the following command to set the environment variables:

```bash
. ./.myenv
```

### Generating an SSL certificate

You can use [Let's Encrypt](https://letsencrypt.org/) to create a free SSL certificate (`fullchain.pem` and `privkey.pem`) for your server. However, for the purpose of testing locally, you can generate your own using the following command

```
openssl req -x509 -newkey rsa:4096 -keyout privkey.pem -out fullchain.pem -sha256 -days 3650 -nodes -subj "/C=XX/ST=StateName/L=CityName/O=CompanyName/OU=CompanySectionName/CN=CommonNameOrHostname"
```

### Generating API configuration

Create a copy of [config.yml.template](/config.yml.template) at the config folder and name it `config.yml`. Update the values accordingly. Note that you must update the secret key for the API by running `openssl rand -hex 32` and update the value in `config.yml`.

### Setup dependencies

**1. Installing required services using Docker:**

List of services: [knowledge graph](https://jena.apache.org/documentation/fuseki2/), our [API](/minmodkg/api.py), [nginx](https://nginx.org)

```
cd ta2-minmod-kg
docker network create minmod
docker compose build
cd ..
```

**2. Installing python library**

Requiring [poetry](https://python-poetry.org/): `pip install poetry`

```
cd ta2-minmod-kg
python -m venv .venv
poetry install --only main
cd ..
```

### Setup Users

You can create a user using the command: `python -m minmodkg.api user -u <username> -n <name> -e <email>` (the password will be prompted).

Additionally, we have an option to load users from a file (.JSON) using the following command: `python -m minmodkg.api load-user <filepath>`. To add a user to a file, run `python -m minmodkg.api add-user <filepath> -u <username> -n <name> -e <email>`. To add multiple users from a CSV file, run `python -m minmodkg.api batch-add-user <filepath> <csvfile>`.

These commands need to be run on a machine that can have access to the database. If you deployed the database inside Docker, you can run the command inside the Docker container with `docker exec -it <container_name> python -m minmodkg.api ...`.

### Database migrations

The API creates missing tables on startup but never adds columns to existing ones, so schema changes to existing tables ship as SQL scripts in `migrations/`. Apply each `NNN_*.up.sql` in order **before** starting the new API; each is idempotent and runs in one transaction. `NNN_*.down.sql` reverts it (and may drop data).

```bash
# Postgres running in Docker (find the container with `docker ps`)
docker exec -i <postgres_container> psql -U minmod -d minmod -v ON_ERROR_STOP=1 < migrations/001_geochem_sample.up.sql

# or directly
psql "postgresql://minmod:<password>@<host>:5432/minmod" -v ON_ERROR_STOP=1 -f migrations/001_geochem_sample.up.sql
```

Lint with `sqlfluff lint migrations/`.

## Usage

<div align="center">
  <img src="/docs/system-overview.png" alt="drawing" width="70%" />
</div>

The figure above denotes how different components work to produce mineral site data and grade tonnage models. The rounded rectangle are systems, blue rectangle are processes, and the parallelogram is the input source.

**1. Building TA2 knowledge graph**

```
source ta2-minmod-kg/.venv/bin/activate
python -m statickg ta2-minmod-kg/etl.yml ./kgdata ta2-minmod-data --overwrite-config --refresh 20
```

Note that this process will continue running to monitor for new changes. Hence, it will not terminate unless a terminating signal is received explicitly.

**1a. Loading GeoChem data**

GeoChem papers are not part of the build above. Their source of truth is a directory of canonical JSON-LD files, one per paper. Load it with a separate loader once the stores from step 1 are up:

```bash
export CFG_FILE=<CFG_DIR>/config.yml   # kgrel and triplestore must point at the stores built in step 1
python -m minmodkg.etl.geochem_loader <jsonld_dir> --entity-dir ta2-minmod-data/data/entities
```

- Each paper becomes a `paper` row and a `:MineralResourcePaper` node. Each of its deposits becomes a mineral site owned by the `geochem-hmi` system user, with `source_id` `https://doi.org/<doi>`, so site ids match what the GeoChem HMI computes. Papers without a DOI are skipped and listed.
- Every run replaces each paper's sites and samples, in Postgres and the triple store, with what its file says. Deposits and samples removed from a file are removed from MinMod. Same-as links made by curators are kept.
- `--entity-dir` resolves ISO country codes. `--paper <paper_id>` reloads selected papers, and `--skip-kg` loads Postgres only.
- Step 1 creates fresh database versions on every full rebuild, so run the loader again after each rebuild.

Edits to GeoChem samples and deposits made through the API are written back into the paper's JSON-LD by the sync service, so they survive a reload. Pass it the directory with `--jsonld-dir`; if the directory is a git repository, the changes are committed and pushed like the data repository's:

```bash
python -m minmodkg.services.sync <ta2-minmod-data> --jsonld-dir <jsonld_dir>
```

The `geochem-hmi` user must exist with the `system` role for the HMI to edit these sites. The `user` command only creates `user`-role accounts, so use `add-user`, set `"role": "system"` in the resulting file, then `load-user` it.

**2. Starting other services**

```bash
docker compose -f ./ta2-minmod-kg/docker-compose.yml up nginx api
```

If you also want to start our [dashboard](https://minmod.isi.edu), run `docker compose up nginx api dashboard` instead. Note that currently, URLs for TA2 services are hardcoded in the dashboard, so it will not query our local services.

Once it starts, you can view our API docs in [https://localhost/api/v1/docs](https://localhost/api/v1/docs)

**3. Upload data to CDR**

To upload data to CDR, you need to obtain a token first. Then, run the following command:

```
export CDR_AUTH_TOKEN=<your cdr token>
export MINMOD_API=https://localhost/api/v1
export MINMOD_SYSTEM=test
python -m minmodkg.integrations.cdr
```

The two environment variables `MINMOD_API` and `MINMOD_SYSTEM` are used for testing purposes. For production, you can simply ignore these two variables and our code will use the default values `MINMOD_API=https://minmod.isi.edu/api/v1` and `MINMOD_SYSTEM=minmod`.

**4. Download data from CDR**

The CDR endpoint for getting TA2 output is [https://api.cdr.land/docs#/Minerals/list_dedup_site_commodity_v1_minerals_dedup_site_search**commodity**get](https://api.cdr.land/docs#/Minerals/list_dedup_site_commodity_v1_minerals_dedup_site_search__commodity__get).

There are two required parameters:

- commodity: name of the commodity (capitalize, case-sensitive) e.g., `Lithium`. Here is [list of commodities](https://github.com/DARPA-CRITICALMAAS/ta2-minmod-data/blob/main/data/entities/commodity.csv)
- system: `minmod`

By default, the CDR should return all sites matched the provided two parameters. To further filter the data for sites with deposit type classification and sites with grade/tonnage data, we can apply the following additional filters:

- Site with deposit type classification: `with_location = true`, and `with_deposit_types_only = true`

  CURL Command:

  ```
  curl -X 'GET' \
  'https://api.cdr.land/v1/minerals/dedup-site/search/Lithium?with_location=true&with_deposit_types_only=true&system=minmod&top_n=1&limit=-1' \
  -H 'Authorization: Bearer <your token>'
  ```

- Site with grade/tonnage data: `with_contained_metals = true`

  CURL Command:

  ```
  curl -X 'GET' \
  'https://api.cdr.land/v1/minerals/dedup-site/search/Lithium?with_contained_metals=true&system=minmod&top_n=1&limit=-1' \
  -H 'Authorization: Bearer <your token>'
  ```

**5. Browsing the data locally**

You can browse the data locally by replacing the hostname from `minmod.isi.edu` to `localhost`. For example, `https://minmod.isi.edu/resource/kg` by `https://localhost/resource/kg`

**6. Querying data**

If you know [SPARQL](https://en.wikipedia.org/wiki/SPARQL), you can query the data by sending your query to `https://<hostname>/sparql`. We have a helper function to make it easier if you know Python in [here](https://github.com/DARPA-CRITICALMAAS/ta2-minmod-kg/blob/04622ff8220a543f84f5207b9d9b1d9f95036888/minmodkg/misc.py#L61)

Also, you can download mineral site data and grade/tonnage model directly from our API. Please see our API docs in [https://localhost/api/v1/docs](https://localhost/api/v1/docs) for more information.
