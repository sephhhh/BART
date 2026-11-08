# BART PES Shift Optimizer

Senior design project with BART. It picks which trains each Passenger Experience Survey (PES) team rides, so teams spend more of their shift surveying riders and less waiting on platforms.

## How the pieces fit

```
S3 (raw xlsx)  ->  build_tables.py  ->  CSV tables  ->  load.sh  ->  PostgreSQL (RDS)  ->  route_engine.py
```

| File | What it does |
|---|---|
| `build_tables.py` | Reads the five BART workbooks and writes 12 clean CSV tables: stations, stop times, ride edges, dayparts, zones, congestion, the existing shift library, surveyors, assignments, events. |
| `schema.sql` | Creates those 12 tables. Safe to rerun: drops and recreates everything. |
| `load.sh` | Creates the `bart` database if needed, runs `schema.sql`, loads the CSVs and adds indexes. |
| `indexes.sql` | Indexes for the route engine's lookups. `load.sh` runs it after loading. |
| `route_engine.py` | Plans routes from the database: the highest-scoring route for a team when it clocks in, a re-plan after a missed train, and scoring of existing shifts for comparison. |
| `bart-shift-demo.html` | Standalone browser demo of the idea: team view, shift generator, coverage tables, route map. It uses **placeholder** train times and is not yet connected to the database or route engine. |

## Data

The workbooks are BART's internal data and are **not** in this repo. They live in the project's S3 bucket. Never commit the xlsx files or the generated CSVs; `.gitignore` excludes them.

`build_tables.py` expects these file names (as uploaded to S3):

- `Weekday-August-2026.xlsx`
- `Weekend-August-2026.xlsx`
- `Copy_of_2026_FastField_Inputs.xlsx`
- `Copy_of_Team_Views.xlsx`
- `Copy_of_Shift_Viewer_.xlsx`

Local copies with spaces in their names (`Copy of 2026 FastField Inputs.xlsx`) must be renamed first.

## Running it

Needs Python 3 and the PostgreSQL client (`psql`).

```bash
pip install openpyxl "psycopg[binary]"

# 1. Build the tables (folder with the 5 xlsx files -> output folder)
python3 build_tables.py data/ tables/

# 2. Load them into PostgreSQL (prompts for the password if PGPASSWORD is not set)
bash load.sh <database-endpoint> <user> tables

# 3. Plan routes (set PGHOST, PGUSER, PGDATABASE=bart, PGSSLMODE=require first)
python3 route_engine.py clock-in --date 2026-11-02 --team "Team A" \
    --start "West Oakland" --end "West Oakland" --from 14:10 --to 20:58
python3 route_engine.py missed --date 2026-11-02 --team "Team A" --at "Bay Fair" --time 17:15
python3 route_engine.py show --date 2026-11-02
python3 route_engine.py compare --shift 314
python3 route_engine.py reset --date 2026-11-02
```

The RDS database is private, so steps 2 and 3 have to run from the project's EC2 instance (or anywhere else inside the same VPC).

Open `bart-shift-demo.html` directly in a browser; it needs no server.

## Not done yet

- A web API (FastAPI) on EC2 that exposes the route engine
- Connecting the demo page to that API in place of its placeholder data
- Hosting the page where the team and BART can open it
