#!/bin/bash
# Load the 12 clean CSV tables into PostgreSQL (RDS or local).
# Usage:   ./load.sh <database-endpoint> [master-user] [tables-folder]
# Example: ./load.sh bart-db.abc123xyz.us-east-1.rds.amazonaws.com postgres tables
set -euo pipefail

HOST="${1:?Give the database endpoint, e.g. ./load.sh bart-db.xxxx.us-east-1.rds.amazonaws.com}"
DBUSER="${2:-postgres}"
DIR="${3:-tables}"
DB="bart"
SSL="${PGSSLMODE:-require}"          # RDS wants SSL; set PGSSLMODE=disable for a local test
HERE="$(cd "$(dirname "$0")" && pwd)"

if [ -z "${PGPASSWORD:-}" ]; then
  read -rsp "Database password for $DBUSER: " PGPASSWORD; echo
  export PGPASSWORD
fi

ADMIN="host=$HOST user=$DBUSER dbname=postgres sslmode=$SSL"
CONN="host=$HOST user=$DBUSER dbname=$DB sslmode=$SSL"

echo "1/4 Creating database '$DB' if needed..."
if ! psql "$ADMIN" -tAc "SELECT 1 FROM pg_database WHERE datname='$DB'" | grep -q 1; then
  psql "$ADMIN" -qc "CREATE DATABASE $DB"
fi

echo "2/4 Creating tables..."
psql "$CONN" -q -v ON_ERROR_STOP=1 -f "$HERE/schema.sql" 2>&1 | grep -v "does not exist, skipping" || true

echo "3/4 Loading CSVs..."
for t in stations stop_times_weekday stop_times_weekend ride_edges dayparts station_zones congestion \
         shift_library shift_legs surveyors assignments events; do
  printf "  %-20s " "$t"
  psql "$CONN" -v ON_ERROR_STOP=1 -c "\copy $t FROM '$DIR/$t.csv' WITH (FORMAT csv, HEADER true)"
done

echo "4/4 Adding indexes..."
psql "$CONN" -q -v ON_ERROR_STOP=1 -f "$HERE/indexes.sql"

echo
echo "Done. Row counts in the database:"
psql "$CONN" -c "
SELECT 'stations' AS table_name, count(*) FROM stations UNION ALL
SELECT 'stop_times_weekday', count(*) FROM stop_times_weekday UNION ALL
SELECT 'stop_times_weekend', count(*) FROM stop_times_weekend UNION ALL
SELECT 'ride_edges', count(*) FROM ride_edges UNION ALL
SELECT 'dayparts', count(*) FROM dayparts UNION ALL
SELECT 'station_zones', count(*) FROM station_zones UNION ALL
SELECT 'congestion', count(*) FROM congestion UNION ALL
SELECT 'shift_library', count(*) FROM shift_library UNION ALL
SELECT 'shift_legs', count(*) FROM shift_legs UNION ALL
SELECT 'surveyors', count(*) FROM surveyors UNION ALL
SELECT 'assignments', count(*) FROM assignments UNION ALL
SELECT 'events', count(*) FROM events;"
