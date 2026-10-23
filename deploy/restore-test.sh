#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 1 || ! -f "$1" ]]; then
  echo 'Usage: bash deploy/restore-test.sh /path/to/backup.dump' >&2
  exit 2
fi
archive="$1"
test_db="opsverse_restore_check"
args=(-f docker-compose.yml -f deploy/compose.live.yml)
created=0
cleanup() {
  if [[ "$created" == 1 ]]; then
    docker compose "${args[@]}" exec -T db psql -U opsverse -d postgres -v ON_ERROR_STOP=1 \
      -c "DROP DATABASE IF EXISTS $test_db WITH (FORCE);" > /dev/null || true
  fi
}
trap cleanup EXIT
docker compose "${args[@]}" exec -T db psql -U opsverse -d postgres -v ON_ERROR_STOP=1 \
  -c "CREATE DATABASE $test_db;" > /dev/null
created=1
docker compose "${args[@]}" exec -T db pg_restore --no-owner --no-acl --exit-on-error \
  -U opsverse -d "$test_db" < "$archive"
docker compose "${args[@]}" exec -T db psql -U opsverse -d "$test_db" -v ON_ERROR_STOP=1 \
  -c 'SELECT version_num FROM alembic_version;'
printf 'RESTORE CHECK PASSED: archive restored to disposable database.\n'
