#!/usr/bin/env bash
# Back up the database, then apply every NNN_*.up.sql in order. Each script
# is idempotent, so this is safe on a database that already has some.
#
# Usage: migrations/apply_all.sh <postgres_container | postgresql://...> [backup_dir]
set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <postgres_container | postgresql://...> [backup_dir]" >&2
    exit 2
fi
target=$1
backup_dir=${2:-.}
here=$(cd "$(dirname "$0")" && pwd)

if [[ $target == postgres*://* ]]; then
    psql=(psql "$target")
    pg_dump=(pg_dump "$target")
    pg_restore=(pg_restore)
    restore_args="-d $target"
else
    psql=(docker exec -i "$target" psql -U minmod -d minmod)
    pg_dump=(docker exec "$target" pg_dump -U minmod -d minmod)
    pg_restore=(docker exec -i "$target" pg_restore)
    restore_args="-U minmod -d minmod"
fi

sql() { "${psql[@]}" -v ON_ERROR_STOP=1 -q "$@"; }
has_table() { [[ $(sql -Atc "SELECT to_regclass('public.$1') IS NOT NULL") == t ]]; }

echo "== Current state"
sql < "$here/status.sql"

read -r -p "Back up and migrate $target? [y/N] " answer
[[ $answer == [yY] ]] || exit 1

backup="$backup_dir/minmod-$(date +%Y%m%d-%H%M%S).dump"
echo "== Backing up to $backup"
"${pg_dump[@]}" -Fc > "$backup"
# a dump that can't be listed is not a backup
"${pg_restore[@]}" --list < "$backup" > /dev/null
echo "$(du -h "$backup" | cut -f1) written"

for file in "$here"/[0-9][0-9][0-9]_*.up.sql; do
    name=$(basename "$file")
    # paper is created by the API or the loader, already with registered_by
    if [[ $name == 004_* ]] && ! has_table paper; then
        echo "== $name: skipped, no paper table yet"
        continue
    fi
    echo "== $name"
    sql < "$file"
done

echo "== New state"
sql < "$here/status.sql"

cat <<EOF

Backup: $backup
Next, once the new API is running:
  python -m minmodkg.etl.geochem_sample_element   # fills sample_element for existing samples
Restore if needed:
  ${pg_restore[*]} ${restore_args} --clean --if-exists < $backup
EOF
