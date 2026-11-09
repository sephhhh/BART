"""
Web API for the BART PES route engine. Wraps route_engine.py and talks to PostgreSQL (RDS).

Run (from this folder):
    uvicorn api:app --reload --port 8000          # development; the React dev server proxies /api here
    uvicorn api:app --host 0.0.0.0 --port 8000    # production on EC2; also serves ../client/dist

Connection settings come from server/.env (see .env.example) or the usual PG* environment variables.
"""
import datetime as dt
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from psycopg_pool import ConnectionPool, PoolTimeout
from pydantic import BaseModel, Field

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")
os.environ.setdefault("PGCONNECT_TIMEOUT", "10")
os.environ.setdefault("PGDATABASE", "bart")

import route_engine as eng  # noqa: E402  (after load_dotenv so the engine sees the same settings)

log = logging.getLogger("bart-api")

pool = ConnectionPool(
    conninfo="",                       # empty: libpq reads PGHOST, PGUSER, PGPASSWORD, PGDATABASE, PGSSLMODE...
    kwargs={"autocommit": True},
    min_size=1, max_size=int(os.environ.get("DB_POOL_SIZE", "5")),
    check=ConnectionPool.check_connection,   # RDS drops idle connections; test each one before use
    open=False,
)
_tables_ready = False


@asynccontextmanager
async def lifespan(app):
    pool.open(wait=False)              # don't block startup if the database is unreachable; /api/health reports it
    yield
    pool.close()


app = FastAPI(title="BART PES Shift Optimizer", lifespan=lifespan)


def db():
    global _tables_ready
    with pool.connection(timeout=15) as conn:
        if not _tables_ready:
            eng.ensure_tables(conn)
            _tables_ready = True
        yield conn


@app.exception_handler(eng.RouteError)
async def route_error(request: Request, exc: eng.RouteError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(PoolTimeout)
@app.exception_handler(psycopg.OperationalError)
async def db_down(request: Request, exc: Exception):
    log.warning("database unavailable: %s", exc)
    return JSONResponse(status_code=503, content={
        "detail": "Can't reach the database. Check server/.env, that the server runs inside the RDS VPC "
                  "(or through an SSH tunnel), and that the RDS security group allows it."})


@app.exception_handler(psycopg.errors.UndefinedTable)
async def no_tables(request: Request, exc: Exception):
    return JSONResponse(status_code=503, content={
        "detail": f"The database is reachable but the BART tables aren't loaded ({exc}). Run load.sh first."})


# ---------- request bodies ----------
class ClockIn(BaseModel):
    date: dt.date
    team: str = Field(min_length=1, max_length=80)
    start: str                          # station code or name
    end: str
    fromMin: int = Field(ge=0, le=2880)  # minutes after midnight
    toMin: int = Field(ge=0, le=2880)


class Missed(BaseModel):
    date: dt.date
    team: str
    at: str                             # station code or name where the team is now
    timeMin: int = Field(ge=0, le=2880)


# ---------- routes ----------
@app.get("/api/health")
def health():
    """Never raises: tells the UI whether the database is reachable and loaded."""
    info = {"database": os.environ.get("PGDATABASE"), "host": os.environ.get("PGHOST", "(not set)")}
    try:
        with pool.connection(timeout=5) as conn:
            counts = conn.execute(
                "SELECT (SELECT count(*) FROM stations), (SELECT count(*) FROM stop_times_weekday), "
                "(SELECT count(*) FROM stop_times_weekend), (SELECT count(*) FROM shift_library)").fetchone()
        return {**info, "ok": True, "tables": dict(zip(
            ["stations", "stop_times_weekday", "stop_times_weekend", "shift_library"], counts))}
    except psycopg.errors.UndefinedTable as e:
        return {**info, "ok": False, "error": f"Tables not loaded yet ({e}). Run load.sh."}
    except PoolTimeout:
        try:                                   # the pool hides why; one direct attempt gives the real reason
            psycopg.connect(connect_timeout=5).close()
            reason = "timed out getting a connection"
        except psycopg.Error as e:
            reason = str(e).strip()
        return {**info, "ok": False, "error": f"Can't reach the database: {reason}"}
    except psycopg.Error as e:
        return {**info, "ok": False, "error": f"Can't reach the database: {e}"}


@app.get("/api/stations")
def stations(conn=Depends(db)):
    rows = conn.execute("SELECT code, name, coalesce(used_as_home, 0) FROM stations ORDER BY name").fetchall()
    return [{"code": c, "name": n, "homeShifts": h} for c, n, h in rows]


@app.get("/api/surveyors")
def surveyors(conn=Depends(db)):
    rows = conn.execute("SELECT name, survey_code, status FROM surveyors ORDER BY name").fetchall()
    return [{"name": n, "code": c, "status": s} for n, c, s in rows]


@app.get("/api/day")
def day_info(date: dt.date, conn=Depends(db)):
    """Assignments and events on file for a date, plus which timetable applies."""
    assignments = conn.execute(
        "SELECT surveyor, shift_or_activity, kind, comment FROM assignments WHERE date=%s ORDER BY surveyor",
        (date,)).fetchall()
    events = conn.execute("SELECT sports, other_event, location FROM events WHERE date=%s", (date,)).fetchall()
    return {
        "date": date.isoformat(), "dayType": eng.day_type_of(date),
        "assignments": [{"surveyor": a, "what": b, "kind": c, "comment": d} for a, b, c, d in assignments],
        "events": [{"sports": a, "other": b, "location": c} for a, b, c in events],
    }


@app.get("/api/routes")
def routes(date: dt.date, team: str | None = None, conn=Depends(db)):
    return eng.routes_for_day(conn, date, team)


@app.post("/api/clock-in")
def clock_in(body: ClockIn, conn=Depends(db)):
    return eng.clock_in(conn, body.date, body.team, body.start, body.end, body.fromMin, body.toMin)


@app.post("/api/missed")
def missed(body: Missed, conn=Depends(db)):
    return eng.missed(conn, body.date, body.team.strip(), body.at, body.timeMin)


@app.delete("/api/routes")
def reset(date: dt.date, team: str | None = None, conn=Depends(db)):
    if team:
        with conn.transaction():
            conn.execute("DELETE FROM team_routes WHERE date=%s AND team=%s", (date, team))
            conn.execute("DELETE FROM team_requests WHERE date=%s AND team=%s", (date, team))
    else:
        eng.reset_day(conn, date)
    return {"ok": True}


@app.get("/api/shifts")
def shifts(conn=Depends(db)):
    rows = conn.execute("""
        SELECT s.shift, s.day_type, s.rides,
               max(l.depart) FILTER (WHERE l.kind = 'clock_in'),
               max(l.depart) FILTER (WHERE l.kind = 'clock_out'),
               (array_agg(st.name ORDER BY l.leg) FILTER (WHERE l.kind = 'ride'))[1]
        FROM shift_library s
        LEFT JOIN shift_legs l ON l.shift = s.shift
        LEFT JOIN stations st ON st.code = l.from_code
        GROUP BY s.shift ORDER BY s.shift""").fetchall()
    return [{"shift": a, "dayType": b, "rides": c, "clockIn": d, "clockOut": e, "home": f}
            for a, b, c, d, e, f in rows]


@app.get("/api/shifts/{shift}/compare")
def compare(shift: int, conn=Depends(db)):
    return eng.compare_shift(conn, shift)


@app.post("/api/reload-timetable")
def reload_timetable():
    """Drop the cached train network, e.g. after re-running load.sh with new timetables."""
    eng.clear_network_cache()
    return {"ok": True}


# ---------- the React app (production build) ----------
DIST = HERE.parent / "client" / "dist"
if DIST.is_dir():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="client")
