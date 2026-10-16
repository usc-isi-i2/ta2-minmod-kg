#!/usr/bin/env bash
#
# Problems 1, 4 and 2 — before/after acceptance capture.
#
#   ./run_acceptance.sh before     # run this BEFORE applying the patches
#   ./run_acceptance.sh after      # run this AFTER the ETL has rebuilt the merged table
#   ./run_acceptance.sh compare    # print the before/after table
#
# READ-ONLY. Every connection sets default_transaction_read_only=on, so the
# session is refused by Postgres if anything tries to write. The queries are
# plain SELECTs. Nothing here applies a patch, restarts a service or touches the
# RDF store.
#
# Output goes to ./acceptance_runs/<before|after>/ so both runs can be kept and
# re-read later.
#
# Aditi Bombe, USC ISI.

set -euo pipefail

MODE="${1:-}"
case "$MODE" in
  before|after|compare) ;;
  *) echo "usage: $0 before|after|compare" >&2; exit 2 ;;
esac

# ---------------------------------------------------------------- configuration
# Override any of these by exporting them first.
REPO_DIR="${REPO_DIR:-./ta2-minmod-kg}"       # the ta2-minmod-kg checkout (for the commit SHA)
KGDATA_DIR="${KGDATA_DIR:-./kgdata}"          # statickg workdir (2nd arg of `python -m statickg`)
# Where the .sql files live. Defaults to an acceptance/ folder next to this
# script, so dropping the script beside the queries just works.
SQL_DIR="${SQL_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/acceptance}"
OUT_ROOT="${OUT_ROOT:-./acceptance_runs}"
PGUSER_="${PGUSER_:-minmod}"
PGDB_="${PGDB_:-minmod}"

# If DATABASE_URL is set we talk to Postgres directly; otherwise we exec into the
# container statickg started, whose name is "kgrel-version-NNN".
PG_CONTAINER="${PG_CONTAINER:-}"

OUT="$OUT_ROOT/$MODE"

# ------------------------------------------------------------------- psql shim
psql_ro() {
  # args are passed through to psql; connection is forced read-only
  if [[ -n "${DATABASE_URL:-}" ]]; then
    PGOPTIONS='-c default_transaction_read_only=on' \
      psql "$DATABASE_URL" -v ON_ERROR_STOP=1 "$@"
  else
    docker exec -i -e PGOPTIONS='-c default_transaction_read_only=on' \
      "$PG_CONTAINER" psql -U "$PGUSER_" -d "$PGDB_" -v ON_ERROR_STOP=1 "$@"
  fi
}

find_container() {
  [[ -n "${DATABASE_URL:-}" ]] && return 0
  [[ -n "$PG_CONTAINER" ]] && return 0
  PG_CONTAINER="$(docker ps --format '{{.Names}}' | grep -E '^kgrel-version-[0-9]+$' | head -1 || true)"
  if [[ -z "$PG_CONTAINER" ]]; then
    echo "ERROR: no running container matching ^kgrel-version-[0-9]+$." >&2
    echo "       Set PG_CONTAINER=<name>, or DATABASE_URL=postgresql://... and re-run." >&2
    echo "       Currently running:" >&2
    docker ps --format '  {{.Names}}' >&2
    exit 1
  fi
  echo "Postgres container: $PG_CONTAINER"
}

# ------------------------------------------------------- the compare-mode table
if [[ "$MODE" == "compare" ]]; then
  B="$OUT_ROOT/before/summary.tsv"
  A="$OUT_ROOT/after/summary.tsv"
  for f in "$B" "$A"; do
    [[ -f "$f" ]] || { echo "ERROR: missing $f — run '$0 before' and '$0 after' first." >&2; exit 1; }
  done

  printf '\n%-42s %14s %14s %12s\n' "check" "before" "after" "change"
  printf '%s\n' "------------------------------------------------------------------------------------"
  # keys are emitted in a fixed order by the summary query, so join on key
  while IFS=$'\t' read -r key before; do
    after="$(awk -F'\t' -v k="$key" '$1==k {print $2}' "$A")"
    [[ -z "$after" ]] && after="?"
    if [[ "$before" =~ ^-?[0-9]+$ && "$after" =~ ^-?[0-9]+$ ]]; then
      delta=$(( after - before ))
      [[ $delta -gt 0 ]] && delta="+$delta"
    else
      delta="-"
    fi
    printf '%-42s %14s %14s %12s\n' "$key" "$before" "$after" "$delta"
  done < "$B"

  echo
  echo "What the reprojection fix (#108) should do, if 'before' predates it:"
  echo "  lat_out_of_range_merged     24,818 -> 1   (Tagaung Taung is swapped in"
  echo "                                             the source JSON; needs a manual"
  echo "                                             fix in ta2-minmod-data, not code)"
  echo "  lat_out_of_range_raw        31,109 -> 1   (the same record)"
  echo "  merged_entities_total        UNCHANGED     <- if this moves, something is wrong"
  echo "  raw_sites_total              UNCHANGED     <- same"
  echo
  echo "What the state repair (fix/p2-state-repair) should do:"
  echo "  state_country_conflicts      5,856 -> 0"
  echo "  merged_with_state            drops by 221  (5,543 states repointed and 92"
  echo "                                               Katanga entities filled from"
  echo "                                               their location; states the"
  echo "                                               rule cannot resolve are"
  echo "                                               dropped, not left wrong)"
  echo "  merged_with_country          UNCHANGED     <- the count only: the country"
  echo "                                               value changes for 80 entities"
  echo "                                               (73 moved to a listed"
  echo "                                               dependency, 7 by the"
  echo "                                               country/state pairing fix)"
  echo
  echo "Full query output, including the five Alaska spot-check sites:"
  echo "  $OUT_ROOT/before/  and  $OUT_ROOT/after/"
  echo
  diff -u "$OUT_ROOT/before/env.txt" "$OUT_ROOT/after/env.txt" \
    && echo "(environment identical between runs)" || true
  exit 0
fi

# --------------------------------------------------------------- capture a run
find_container
mkdir -p "$OUT"

echo "=== $MODE  ($(date -u '+%Y-%m-%dT%H:%M:%SZ')) ==="

# 1. Environment. The merge-cache filename is the single most diagnostic fact
#    here: if "after" does not list merge-v108, the merge was skipped and the
#    numbers below cannot have changed.
{
  echo "timestamp_utc      $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "hostname           $(hostname)"
  echo "pg_container       $PG_CONTAINER"
  if [[ -d "$REPO_DIR/.git" ]]; then
    echo "ta2-minmod-kg      $(git -C "$REPO_DIR" rev-parse HEAD)"
    echo "ta2-minmod-kg br   $(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD)"
  else
    echo "ta2-minmod-kg      (not a git checkout at $REPO_DIR)"
  fi
  echo -n "merge cache        "
  ls "$KGDATA_DIR/services/mineralsiteetl/" 2>/dev/null | grep -E '^merge-v[0-9]+\.sqlite$' | tr '\n' ' ' || echo -n "(not found)"
  echo
  echo -n "kgrel db dirs      "
  ls -d "$KGDATA_DIR"/databases/kgrel/version-* 2>/dev/null | xargs -n1 basename 2>/dev/null | tr '\n' ' ' || echo -n "(not found)"
  echo
  echo -n "free disk          "
  df -h . | tail -1
} | tee "$OUT/env.txt"

echo
echo "--- summary counts ---"

# 2. Machine-readable summary, one metric per line, fixed order.
psql_ro -At -F $'\t' <<'SQL' | tee "$OUT/summary.tsv"
SELECT 'lat_out_of_range_merged', count(*)::text FROM dedup_mineral_site
  WHERE coordinates IS NOT NULL
    AND abs((convert_from(coordinates, 'UTF8')::jsonb -> 'value' ->> 'lat')::float8) > 90
UNION ALL
SELECT 'lat_out_of_range_raw', count(*)::text FROM mineral_site
  WHERE location_view IS NOT NULL
    AND abs((convert_from(location_view, 'UTF8')::jsonb ->> 'lat')::float8) > 90
UNION ALL
SELECT 'state_country_conflicts', count(*)::text FROM dedup_mineral_site AS d
  WHERE cardinality(d.country_val) > 0
    AND EXISTS (SELECT 1 FROM unnest(d.state_or_province_val) AS s (id)
                INNER JOIN state_or_province AS sp ON s.id = sp.id
                WHERE sp.country IS NOT NULL AND NOT sp.country = any(d.country_val))
UNION ALL
SELECT 'merged_entities_total', count(*)::text FROM dedup_mineral_site
UNION ALL
SELECT 'raw_sites_total', count(*)::text FROM mineral_site
UNION ALL
SELECT 'merged_with_state', count(*)::text FROM dedup_mineral_site
  WHERE cardinality(state_or_province_val) > 0
UNION ALL
SELECT 'merged_with_country', count(*)::text FROM dedup_mineral_site
  WHERE cardinality(country_val) > 0
UNION ALL
SELECT 'state_table_rows', count(*)::text FROM state_or_province
UNION ALL
SELECT 'state_table_with_code', coalesce(count(*) FILTER (
    WHERE to_jsonb(sp) ? 'state_code' AND to_jsonb(sp) ->> 'state_code' IS NOT NULL), 0)::text
  FROM state_or_province AS sp;
SQL

# 3. Full output of the committed acceptance queries, for the record.
echo
for q in problem1_latitude_range problem2_state_country_conflict problem4_nad27_spotcheck ; do
  f="$SQL_DIR/$q.sql"
  if [[ ! -f "$f" ]]; then
    echo "SKIP $q.sql (not found at $f — set SQL_DIR=<dir holding the .sql files>)" \
      | tee -a "$OUT/queries.txt"
    continue
  fi
  echo "--- $q ---" | tee -a "$OUT/queries.txt"
  psql_ro -f - < "$f" | tee -a "$OUT/queries.txt"
  echo | tee -a "$OUT/queries.txt"
done

echo
echo "Written to $OUT/"
echo "When both runs are done:  $0 compare"
