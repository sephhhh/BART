# BART PES Shift Optimizer

Senior design project with BART. It picks which trains each Passenger Experience Survey (PES) team rides, so teams spend more of their shift surveying riders and less waiting on platforms.

## How the pieces fit

```
S3 (raw xlsx) -> build_tables.py -> CSV tables -> load.sh -> PostgreSQL (RDS)
                                                                  ^
                                   React app (client/) -> FastAPI (server/api.py) -> route_engine.py
```

| Path | What it does |
|---|---|
| `build_tables.py` | Reads the five BART workbooks and writes 12 clean CSV tables: stations, stop times, ride edges, dayparts, zones, congestion, the existing shift library, surveyors, assignments, events. |
| `schema.sql` | Creates those 12 tables. Safe to rerun: drops and recreates everything. |
| `load.sh` | Creates the `bart` database if needed, runs `schema.sql`, loads the CSVs and adds indexes. |
| `indexes.sql` | Indexes for the route engine's lookups. `load.sh` runs it after loading. |
| `server/route_engine.py` | Plans routes from the database: the highest-scoring route for a team when it clocks in, a re-plan after a missed train, and scoring of existing shifts. Usable as a CLI and as a library. |
| `server/api.py` | FastAPI web API around the route engine. In production it also serves the built React app. |
| `client/` | React app (Vite): Team view, Day board, Existing shifts. |
| `bart-shift-demo.html` | The original standalone prototype with placeholder data. Superseded by `client/`; kept for reference. |

### The app

- **Team view**: a team clocks in (team name, start/end station, shift times) and gets its route, leg by leg, drawn on the BART map. "Missed it" on any upcoming ride re-plans the rest of the shift from that station; "Re-plan from where you are" does the same from any station and time. Re-planned legs turn blue.
- **Day board**: every team's route for a date, totals, minutes on board by line and hour, and the assignments and events on file. Delete one team's route or reset the day.
- **Existing shifts**: the current hand-built shift library, each shift scored with the engine's rules so you can compare it with the engine's routes.

### API

| Method | Path | |
|---|---|---|
| GET | `/api/health` | Database reachable? Tables loaded? (never errors; the UI shows its message) |
| GET | `/api/stations`, `/api/surveyors` | Lookup lists |
| GET | `/api/day?date=` | Assignments and events for a date |
| GET | `/api/routes?date=[&team=]` | Planned routes |
| POST | `/api/clock-in` | `{date, team, start, end, fromMin, toMin}` → planned route |
| POST | `/api/missed` | `{date, team, at, timeMin}` → re-planned route |
| DELETE | `/api/routes?date=[&team=]` | Delete one team's route, or the whole day |
| GET | `/api/shifts`, `/api/shifts/{id}/compare` | Existing shift library and a scored shift |
| POST | `/api/reload-timetable` | Forget the cached timetable after re-running `load.sh` |

Times are minutes after midnight (`14:10` = 850); values past 1440 are after midnight. Interactive docs at `/docs` while the server runs.

## Data

The workbooks are BART's internal data and are **not** in this repo. They live in the project's S3 bucket. Never commit the xlsx files, the generated CSVs, or `server/.env`; `.gitignore` excludes them.

`build_tables.py` expects these file names (as uploaded to S3):

- `Weekday-August-2026.xlsx`
- `Weekend-August-2026.xlsx`
- `Copy_of_2026_FastField_Inputs.xlsx`
- `Copy_of_Team_Views.xlsx`
- `Copy_of_Shift_Viewer_.xlsx`

Local copies with spaces in their names (`Copy of 2026 FastField Inputs.xlsx`) must be renamed first.

## Setup

Needs Python 3.10+, Node 18+, and the PostgreSQL client (`psql`).

### 1. Load the database (once, and again when the data changes)

```bash
pip install openpyxl "psycopg[binary]"
python3 build_tables.py data/ tables/                  # folder with the 5 xlsx files -> output folder
bash load.sh <rds-endpoint> <master-user> tables       # prompts for the password
```

### 2. Database credentials

```bash
cp server/.env.example server/.env
```

Fill in `PGHOST` (the RDS endpoint), `PGUSER`, `PGPASSWORD`. `PGDATABASE=bart` and `PGSSLMODE=require` are already set. The API, `route_engine.py` and `psql` all read these.

The RDS instance is private, so the API must run where it can reach it:

- **On the project's EC2 instance** (same VPC). The RDS security group must allow inbound 5432 from the EC2 instance's security group.
- **From your laptop, through an SSH tunnel via EC2.** Leave this running in a terminal:
  ```bash
  ssh -i your-key.pem -N -L 5432:<rds-endpoint>:5432 ec2-user@<ec2-public-ip>
  ```
  and set `PGHOST=localhost` in `server/.env`. (`PGSSLMODE=require` still works through the tunnel.)

### 3. Run it (development)

```bash
# terminal 1: API on :8000
cd server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn api:app --reload

# terminal 2: React app on :5173 (forwards /api to :8000)
cd client
npm install
npm run dev
```

Open http://localhost:5173. The header says whether the database is connected; if not, it shows the exact error.

### 4. Run it (production, on EC2)

```bash
cd client && npm install && npm run build      # writes client/dist
cd ../server && source .venv/bin/activate
uvicorn api:app --host 0.0.0.0 --port 8000     # serves the app and the API together
```

Open port 8000 in the EC2 security group (ideally only to your team's IPs), or put nginx in front on port 80/443. To keep it running after you log out, use `systemd` or `tmux`. There's no login yet, so anyone who can reach the port can plan and delete routes.

### Command line

The route engine still works on its own (reads the same `server/.env`):

```bash
cd server
python3 route_engine.py clock-in --date 2026-11-02 --team "Team A" \
    --start "West Oakland" --end "West Oakland" --from 14:10 --to 20:58
python3 route_engine.py missed --date 2026-11-02 --team "Team A" --at "Bay Fair" --time 17:15
python3 route_engine.py show --date 2026-11-02
python3 route_engine.py compare --shift 314
python3 route_engine.py reset --date 2026-11-02
```

## Not done yet

- Sign-in (teams and dispatchers), so only the team can change its own route
- HTTPS and a stable URL for the team and BART
- Logging clock-in/out, breaks and arrivals (the FastField submissions) in the app
