"""
BART PES route engine, running on the real data in PostgreSQL (RDS).

Each clock-in gets the highest-scoring route for that team, given every route already handed
out that day. A missed train re-plans only that team. Routes are saved in the database.

The functions here return plain dicts, so both the CLI below and the web API (api.py) use them.

Connection: set the usual PostgreSQL environment variables first (or put them in server/.env), e.g.
    export PGHOST=bart-rds.xxxx.us-east-1.rds.amazonaws.com PGUSER=BART_MASTER PGDATABASE=bart PGSSLMODE=require
    export PGPASSWORD='...'          (or leave it out and psycopg reads ~/.pgpass)

Commands:
    python3 route_engine.py clock-in --date 2026-11-02 --team "Julio+Eric" \
        --start "West Oakland" --end "West Oakland" --from 14:10 --to 20:58
    python3 route_engine.py missed --date 2026-11-02 --team "Julio+Eric" --at "Bay Fair" --time 17:15
    python3 route_engine.py show --date 2026-11-02 [--team "Julio+Eric"]
    python3 route_engine.py compare --shift 314          (score an existing shift the same way)
    python3 route_engine.py reset --date 2026-11-02      (delete that day's routes)

Needs: pip install -r requirements.txt
"""
import argparse, bisect, datetime as dt, heapq, sys, threading
from collections import defaultdict

import psycopg

# ---------- settings (starting values; confirm with BART) ----------
BUFFER = 5                     # minutes to change trains
CLOCK_IN = 15                  # clock-in to earliest train
CLOCK_OUT = 5                  # last arrival to clock-out
DISCOUNT = 0.5                 # points multiplier per earlier team on the same direction + hour
TRANSFER_COST = 1.0            # small penalty per train change, avoids pointless hops
# Breaks, from the existing shift library: (minutes, earliest, latest) after clock-in
BREAKS_LONG = [(15, 50, 160), (45, 160, 260), (15, 300, 370)]   # shifts of 6 hours or more
BREAK_STATIONS = {"K20", "R40", "M10", "A50", "M16", "A10", "M90", "C30", "K10", "K30", "M40", "R20", "R30", "C40"}
MISSING_DAYPART_WEIGHT = 1.0   # no congestion rating (weekend after 8 PM): treat as Open

ROUTES_DDL = """
CREATE TABLE IF NOT EXISTS team_routes (
    date         date NOT NULL,
    team         text NOT NULL,
    leg          int  NOT NULL,
    kind         text NOT NULL,            -- clock_in / ride / break / clock_out
    from_code    text,
    to_code      text,
    depart_min   int,
    arrive_min   int,
    trip_id      text,
    direction    text,
    points       numeric,
    status       text NOT NULL DEFAULT 'planned',   -- planned / replaced
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS team_requests (
    date date NOT NULL, team text NOT NULL, start_code text, end_code text,
    from_min int, to_min int, PRIMARY KEY (date, team)
);
"""


class RouteError(Exception):
    """A request the engine can't serve (unknown station, no valid route, ...). Shown to the user as-is."""


def hm(m):
    return f"{m // 60:02d}:{m % 60:02d}"


def parse_hm(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def day_type_of(date):
    return "weekend" if date.weekday() >= 5 else "weekday"


# ---------- network for one day type ----------
class Network:
    def __init__(self, conn, day_type):
        self.day_type = day_type
        cur = conn.cursor()
        cur.execute("SELECT code, name, ff_name FROM stations")
        self.names, self.lookup = {}, {}
        for code, name, ff in cur.fetchall():
            self.names[code] = name
            for key in (code, name, ff):
                if key:
                    self.lookup[key.strip().lower()] = code
        table = "stop_times_weekday" if day_type == "weekday" else "stop_times_weekend"
        cur.execute(f"SELECT trip_id, route, line, direction, stop_seq, station_code, time_min FROM {table} "
                    "ORDER BY trip_id, stop_seq")
        self.trips = defaultdict(list)            # trip -> [(station, time)]
        self.trip_dir = {}
        for trip, route, line, direction, seq, code, t in cur.fetchall():
            self.trips[trip].append((code, t))
            self.trip_dir[trip] = (line, direction)
        # departure events per station
        events = defaultdict(set)
        self.boardings = defaultdict(list)       # (station, time) -> [(trip, stop index)]
        for trip, stops in self.trips.items():
            for i, (code, t) in enumerate(stops[:-1]):
                events[code].add(t)
                self.boardings[(code, t)].append((trip, i))
        self.events = {c: sorted(ts) for c, ts in events.items()}
        # crowd weight per segment: zone of the segment's first station, daypart of its time
        cur.execute("SELECT line, station_code, zone FROM station_zones")
        zone = {(l, c): z for l, c, z in cur.fetchall()}
        cur.execute("SELECT daypart, start_min, end_min FROM dayparts WHERE day_type = %s ORDER BY start_min",
                    (day_type,))
        self.dayparts = cur.fetchall()
        cur.execute("SELECT direction, zone, daypart, weight FROM congestion")
        cong = {(d, z, p): float(w) for d, z, p, w in cur.fetchall()}
        self.seg_weight = {}                     # (trip, i) -> weight of segment i -> i+1
        for trip, stops in self.trips.items():
            line, direction = self.trip_dir[trip]
            for i in range(len(stops) - 1):
                code, t = stops[i]
                z, p = zone.get((line, code)), self.daypart(t)
                self.seg_weight[(trip, i)] = cong.get((direction, z, p), MISSING_DAYPART_WEIGHT) if z else 1.0

    def daypart(self, t):
        t = t % 1440
        for p, a, b in self.dayparts:
            if a <= t <= b:
                return p
        return None

    def code(self, name):
        c = self.lookup.get(str(name).strip().lower())
        if not c:
            raise RouteError(f"Unknown station: {name!r}")
        return c

    def next_event(self, station, t):
        ev = self.events.get(station, [])
        i = bisect.bisect_left(ev, t)
        return ev[i] if i < len(ev) else None


# The timetable doesn't change while the app runs, so each day type's network is built once per process.
_NETWORKS, _NETWORK_LOCK = {}, threading.Lock()


def network(conn, date=None, day_type=None):
    day_type = day_type or day_type_of(date)
    with _NETWORK_LOCK:
        if day_type not in _NETWORKS:
            _NETWORKS[day_type] = Network(conn, day_type)
        return _NETWORKS[day_type]


def clear_network_cache():
    """Call after reloading the timetable tables."""
    with _NETWORK_LOCK:
        _NETWORKS.clear()


# ---------- shared state for the day ----------
def load_state(conn, date, exclude_team=None, keep_before=None):
    """Taken trips and coverage counts from every committed route that day (optionally minus one team's future)."""
    cur = conn.cursor()
    cur.execute("SELECT team, trip_id, depart_min, arrive_min, direction FROM team_routes "
                "WHERE date = %s AND status = 'planned' AND kind = 'ride'", (date,))
    taken, coverage = defaultdict(list), defaultdict(int)
    for team, trip, dep, arr, direction in cur.fetchall():
        if team == exclude_team and (keep_before is None or dep >= keep_before):
            continue
        taken[trip].append((dep, arr, team))
        for hour in range(dep // 60, (arr - 1) // 60 + 1):
            coverage[(direction, hour)] += 1
    return taken, coverage


# ---------- the DP ----------
def best_route(net, team, start_code, start_t, end_code, end_t, taken, coverage,
               breaks_done=0, shift_start=None, discount=DISCOUNT):
    """Highest-scoring route from (start_code, start_t) to end_code by end_t.

    Node = (time, station, breaks taken) for "standing on the platform, ready for the train at this time".
    A node with a 4th field "B" is "just finished a break here". Every edge goes forward in time, so
    popping nodes in time order makes each node's score final before it is used (DP on a DAG).
    """
    shift_start = start_t if shift_start is None else shift_start
    breaks = BREAKS_LONG if (end_t - shift_start) >= 360 else BREAKS_LONG[:1]
    need = len(breaks)
    best, parent, heap, ends = {}, {}, [], []

    def push(node, score, prev, legs):
        if score > best.get(node, float("-inf")):
            best[node] = score
            parent[node] = (prev, legs)
            heapq.heappush(heap, node)

    first = net.next_event(start_code, start_t)
    if first is None:
        return None
    push((first, start_code, breaks_done), 0.0, None, [])

    while heap:
        node = heapq.heappop(heap)
        score = best[node]
        if len(node) == 4:                                     # just finished a break
            t, station, k, _ = node
            if station == end_code and k == need:
                ends.append((score, node, [], t))
            nxt = net.next_event(station, t)
            if nxt is not None and nxt <= end_t:
                push((nxt, station, k), score, node, [])
            continue
        t, station, k = node
        nxt = net.next_event(station, t + 1)                    # let this train go, wait for the next
        if nxt is not None and nxt <= end_t:
            push((nxt, station, k), score, node, [])
        for trip, i in net.boardings.get((station, t), []):    # board a train leaving now
            stops = net.trips[trip]
            direction = net.trip_dir[trip][1]
            gained = 0.0
            for j in range(i + 1, len(stops)):
                t0, t1 = stops[j - 1][1], stops[j][1]
                if t1 > end_t:
                    break
                if any(p != team and d < t1 and t0 < a for d, a, p in taken.get(trip, [])):
                    break                                       # another team is on this train here
                gained += (t1 - t0) * net.seg_weight[(trip, j - 1)] * discount ** coverage.get((direction, t0 // 60), 0)
                to = stops[j][0]
                ride = ("ride", trip, station, t, to, t1, round(gained, 2))
                s2 = score + gained - TRANSFER_COST
                if to == end_code and k == need:               # could finish here
                    ends.append((s2, node, [ride], t1))
                if k < need and to in BREAK_STATIONS:          # could take the next break here
                    length, lo, hi = breaks[k]
                    if lo <= t1 - shift_start <= hi and t1 + length <= end_t:
                        push((t1 + length, to, k + 1, "B"), s2, node, [ride, ("break", to, t1, t1 + length)])
                nxt = net.next_event(to, t1 + BUFFER)          # or change trains (5-minute buffer)
                if nxt is not None and nxt <= end_t:
                    push((nxt, to, k), s2, node, [ride])
    if not ends:
        return None
    score, node, legs, _ = max(ends, key=lambda e: (e[0], e[3]))
    legs = list(legs)
    while node is not None:
        prev, edge_legs = parent[node]
        legs[:0] = edge_legs
        node = prev
    return score, legs


# ---------- turning legs into dicts ----------
def leg_dict(net, kind, frm, to, dep, arr, trip=None, points=None, replanned=False):
    line, direction = net.trip_dir.get(trip, (None, None)) if trip else (None, None)
    return {"kind": kind, "from": frm, "fromName": net.names.get(frm, frm), "to": to, "toName": net.names.get(to, to),
            "depart": dep, "arrive": arr, "tripId": trip, "line": line, "direction": direction,
            "points": None if points is None else round(float(points), 2), "replanned": replanned}


def engine_legs_to_dicts(net, legs, replanned=False):
    out = []
    for l in legs:
        if l[0] == "ride":
            _, trip, a, ta, b, tb, pts = l
            out.append(leg_dict(net, "ride", a, b, ta, tb, trip, pts, replanned))
        else:
            _, st, ta, tb = l
            out.append(leg_dict(net, "break", st, st, ta, tb, points=0, replanned=replanned))
    return out


def route_dict(net, team, legs, t0, t1, start_code, end_code):
    """legs: dicts from engine_legs_to_dicts (rides and breaks only). Adds clock in/out and summary numbers."""
    rides = [l for l in legs if l["kind"] == "ride"]
    onboard = sum(l["arrive"] - l["depart"] for l in rides)
    span = (t1 - t0) if t0 is not None and t1 is not None else 0
    gaps = [b["depart"] - a["arrive"] for a, b in zip(rides, rides[1:])]
    full = ([leg_dict(net, "clock_in", start_code, start_code, t0, t0)] if t0 is not None else []) + legs + \
           ([leg_dict(net, "clock_out", end_code, end_code, t1, t1)] if t1 is not None else [])
    return {
        "team": team,
        "start": start_code, "startName": net.names.get(start_code, start_code),
        "end": end_code, "endName": net.names.get(end_code, end_code),
        "clockIn": t0, "clockOut": t1,
        "legs": full,
        "stats": {
            "onboardMin": onboard, "spanMin": span,
            "onboardPct": round(100 * onboard / span) if span else 0,
            "rides": len(rides), "breaks": sum(1 for l in legs if l["kind"] == "break"),
            "lines": sorted({l["line"] for l in rides if l["line"]}),
            "points": round(sum(l["points"] or 0 for l in rides), 1),
            "shortestTransfer": min(gaps) if gaps else None,
        },
    }


# ---------- database actions ----------
def save(conn, net, date, team, start_code, end_code, t0, t1, legs, first_leg_no=1, clock_in=True):
    cur = conn.cursor()
    rows = []
    n = first_leg_no
    if clock_in:
        rows.append((date, team, 0, "clock_in", start_code, start_code, t0, t0, None, None, None))
    for leg in legs:
        if leg[0] == "ride":
            _, trip, a, ta, b, tb, pts = leg
            rows.append((date, team, n, "ride", a, b, ta, tb, trip, net.trip_dir[trip][1], pts))
        else:
            _, st, ta, tb = leg
            rows.append((date, team, n, "break", st, st, ta, tb, None, None, 0))
        n += 1
    rows.append((date, team, 99, "clock_out", end_code, end_code, t1, t1, None, None, None))
    cur.executemany("INSERT INTO team_routes (date, team, leg, kind, from_code, to_code, depart_min, arrive_min, "
                    "trip_id, direction, points) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows)


def lock(conn):
    conn.execute("LOCK TABLE team_routes IN SHARE ROW EXCLUSIVE MODE")   # one clock-in at a time


def ensure_tables(conn):
    conn.execute(ROUTES_DDL)


def clock_in(conn, date, team, start, end, t0, t1):
    """Plan and save a team's route. start/end: station code or name. t0/t1: minutes after midnight."""
    team = team.strip()
    if not team:
        raise RouteError("Give a team name.")
    if t1 <= t0:
        t1 += 1440                                             # shift ends after midnight
    net = network(conn, date)
    s, e = net.code(start), net.code(end)
    balanced = False
    with conn.transaction():
        lock(conn)
        conn.execute("DELETE FROM team_routes WHERE date = %s AND team = %s", (date, team))
        taken, coverage = load_state(conn, date)
        res = best_route(net, team, s, t0 + CLOCK_IN, e, t1 - CLOCK_OUT, taken, coverage, shift_start=t0)
        if res is None:
            raise RouteError("No valid route for that window (too short, or every train is taken).")
        avg = [r[0] for r in conn.execute(
            "SELECT sum(points) FROM team_routes WHERE date = %s AND kind = 'ride' AND status = 'planned' "
            "GROUP BY team", (date,)).fetchall()]
        if avg and res[0] < 0.75 * float(sum(avg)) / len(avg):     # balance check
            softer = best_route(net, team, s, t0 + CLOCK_IN, e, t1 - CLOCK_OUT, taken, coverage,
                                shift_start=t0, discount=0.75)
            if softer and softer[0] > res[0]:
                res, balanced = softer, True
        conn.execute("INSERT INTO team_requests VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (date, team) DO UPDATE "
                     "SET start_code=EXCLUDED.start_code, end_code=EXCLUDED.end_code, from_min=EXCLUDED.from_min, "
                     "to_min=EXCLUDED.to_min", (date, team, s, e, t0, t1))
        save(conn, net, date, team, s, e, t0, t1, res[1])
    route = route_dict(net, team, engine_legs_to_dicts(net, res[1]), t0, t1, s, e)
    route["balanced"] = balanced
    return route


def missed(conn, date, team, at, now):
    """Re-plan a team's remaining shift from station `at` at minute `now`. Other teams stay locked."""
    net = network(conn, date)
    here = net.code(at)
    with conn.transaction():
        lock(conn)
        req = conn.execute("SELECT start_code, end_code, from_min, to_min FROM team_requests "
                           "WHERE date = %s AND team = %s", (date, team)).fetchone()
        if not req:
            raise RouteError("That team has not clocked in today.")
        s, e, t0, t1 = req
        if now < t0 and now + 1440 <= t1:
            now += 1440                                        # after midnight on a late shift
        breaks_done = conn.execute("SELECT count(*) FROM team_routes WHERE date=%s AND team=%s AND kind='break' "
                                   "AND status='planned' AND arrive_min <= %s", (date, team, now)).fetchone()[0]
        # keep what was ridden, drop this team's future legs; everyone else stays locked
        conn.execute("UPDATE team_routes SET status='replaced' WHERE date=%s AND team=%s AND status='planned' "
                     "AND (depart_min >= %s OR kind='clock_out')", (date, team, now))
        taken, coverage = load_state(conn, date)
        res = best_route(net, team, here, now, e, t1 - CLOCK_OUT, taken, coverage,
                         breaks_done=breaks_done, shift_start=t0)
        if res is None:
            conn.execute("INSERT INTO team_routes (date, team, leg, kind, from_code, to_code, depart_min, arrive_min) "
                         "VALUES (%s,%s,99,'clock_out',%s,%s,%s,%s)", (date, team, e, e, t1, t1))
            return {"replanned": False,
                    "message": f"No way to finish the planned route from {net.names[here]}; "
                               f"head to {net.names[e]} directly."}
        last = conn.execute("SELECT coalesce(max(leg), 0) FROM team_routes WHERE date=%s AND team=%s AND "
                            "status='planned' AND leg < 99", (date, team)).fetchone()[0]
        save(conn, net, date, team, here, e, now, t1, res[1], first_leg_no=last + 1, clock_in=False)
    return {"replanned": True,
            "message": f"Re-planned {team} from {net.names[here]} at {hm(now % 1440)}.",
            "route": routes_for_day(conn, date, team)[0]}


def routes_for_day(conn, date, team=None):
    """Every team's planned route that day (or one team's), with legs and summary numbers."""
    net = network(conn, date)
    q = ("SELECT team, leg, kind, from_code, to_code, depart_min, arrive_min, trip_id, points, created_at "
         "FROM team_routes WHERE date=%s AND status='planned'" + (" AND team=%s" if team else "") +
         " ORDER BY team, leg")
    rows = conn.execute(q, (date, team) if team else (date,)).fetchall()
    by = defaultdict(list)
    for r in rows:
        by[r[0]].append(r)
    out = []
    for name, lst in by.items():
        first_created = min(r[9] for r in lst)
        cin = next((r for r in lst if r[2] == "clock_in"), None)
        cout = next((r for r in lst if r[2] == "clock_out"), None)
        legs = [leg_dict(net, r[2], r[3], r[4], r[5], r[6], r[7], r[8], replanned=r[9] > first_created)
                for r in lst if r[2] in ("ride", "break")]
        out.append(route_dict(net, name, legs, cin[5] if cin else None, cout[5] if cout else None,
                              (cin or lst[0])[3], (cout or lst[-1])[4]))
    return out


def reset_day(conn, date):
    with conn.transaction():
        conn.execute("DELETE FROM team_routes WHERE date=%s", (date,))
        conn.execute("DELETE FROM team_requests WHERE date=%s", (date,))


def compare_shift(conn, shift):
    """Score an existing shift with the same rules, to compare with the engine."""
    row = conn.execute("SELECT day_type FROM shift_library WHERE shift=%s", (shift,)).fetchone()
    if not row:
        raise RouteError(f"Unknown shift {shift}.")
    net = network(conn, day_type=row[0])
    rows = conn.execute("SELECT kind, from_code, to_code, depart, arrive, matched_trip_id FROM shift_legs "
                        "WHERE shift=%s", (shift,)).fetchall()
    t0 = next((parse_hm(r[3]) for r in rows if r[0] == "clock_in" and r[3]), None)
    t1 = next((parse_hm(r[3]) for r in rows if r[0] == "clock_out" and r[3]), None)
    if t0 is not None and t1 is not None and t1 <= t0:
        t1 += 1440                                             # shift ends after midnight
    # Leg numbers in the shift library aren't always in time order, so sort rides by departure.
    late = lambda t: t + 1440 if t0 is not None and t < t0 - 60 else t
    rides = sorted((late(parse_hm(d)), late(parse_hm(a)), f, to, tr, d, a)
                   for k, f, to, d, a, tr in rows if k == "ride" and d and a)
    legs, skipped = [], []
    start = rides[0][2] if rides else None
    end = rides[-1][3] if rides else None
    for ta, tb, frm, to, trip, dep, arr in rides:
        if tb < ta:
            tb += 1440
        stops = net.trips.get(trip) if trip else None
        i = next((k for k, (c, t) in enumerate(stops or []) if c == frm and t == ta), None)
        if i is None:
            skipped.append(f"{net.names.get(frm, frm)} {dep} to {net.names.get(to, to)} {arr}")
            continue
        pts = 0.0
        for j in range(i + 1, len(stops)):
            pts += (stops[j][1] - stops[j - 1][1]) * net.seg_weight[(trip, j - 1)]
            if stops[j][0] == to:
                break
        legs.append(("ride", trip, frm, ta, to, tb, round(pts, 2)))
    route = route_dict(net, f"shift {shift}", engine_legs_to_dicts(net, legs), t0, t1, start, end)
    route["shift"] = shift
    route["dayType"] = row[0]
    route["skipped"] = skipped
    route["issues"] = [f"Leg {leg} ({dep or '?'} {net.names.get(f, f or '')} → {net.names.get(to, to or '')}): {iss}"
                       for leg, dep, f, to, iss in conn.execute(
                           "SELECT leg, depart, from_code, to_code, issues FROM shift_legs "
                           "WHERE shift=%s AND issues IS NOT NULL AND issues <> '' ORDER BY depart, leg",
                           (shift,)).fetchall()]
    if t0 is not None and rides and rides[-1][1] > (t1 or 10**6):
        route["issues"].insert(0, "Rides continue after the clock-out time in the shift library.")
    return route


# ---------- CLI ----------
def print_route(r, title, start_label="clock in at"):
    print(f"\n{title}: {r['team']}")
    print(f"  {'Leg':>3}  {'Board':<24} {'Exit at':<24} {'Depart':>6} {'Arrive':>6}  {'Line':<20} Points")
    n = 0
    for l in r["legs"]:
        if l["kind"] == "clock_in":
            print(f"  {0:>3}  {'IN':<24} {'':<24} {hm(l['depart']):>6} {hm(l['depart']):>6}  "
                  f"{start_label} {l['fromName']}")
        elif l["kind"] == "clock_out":
            print(f"  {99:>3}  {'OUT':<24} {'':<24} {hm(l['depart']):>6} {hm(l['depart']):>6}  "
                  f"clock out at {l['fromName']}")
        elif l["kind"] == "ride":
            n += 1
            print(f"  {n:>3}  {l['fromName'][:24]:<24} {l['toName'][:24]:<24} {hm(l['depart']):>6} "
                  f"{hm(l['arrive']):>6}  {l['direction']:<20} {l['points']:6.1f}")
        else:
            n += 1
            print(f"  {n:>3}  {'':<24} {'':<24} {hm(l['depart']):>6} {hm(l['arrive']):>6}  "
                  f"{l['arrive'] - l['depart']}-minute break at {l['fromName']}")
    s = r["stats"]
    print(f"  On board {s['onboardMin']} of {s['spanMin']} min ({s['onboardPct']}%), {s['rides']} rides, "
          f"lines: {', '.join(s['lines'])}, points {s['points']:.1f}"
          + (f", shortest transfer {s['shortestTransfer']} min" if s["shortestTransfer"] is not None else ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("clock-in")
    for f in ("--date", "--team", "--start", "--end", "--from", "--to"):
        c.add_argument(f, required=True)
    m = sub.add_parser("missed")
    for f in ("--date", "--team", "--at", "--time"):
        m.add_argument(f, required=True)
    s = sub.add_parser("show")
    s.add_argument("--date", required=True)
    s.add_argument("--team")
    k = sub.add_parser("compare")
    k.add_argument("--shift", type=int, required=True)
    r = sub.add_parser("reset")
    r.add_argument("--date", required=True)
    a = ap.parse_args()
    try:
        from dotenv import load_dotenv                       # optional: read server/.env like the API does
        import os
        load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    except ImportError:
        pass
    try:
        with psycopg.connect(autocommit=True) as conn:
            ensure_tables(conn)
            if a.cmd in ("clock-in", "missed", "show", "reset"):
                date = dt.date.fromisoformat(a.date)
            if a.cmd == "clock-in":
                route = clock_in(conn, date, a.team, a.start, a.end, parse_hm(a.__dict__["from"]), parse_hm(a.to))
                if route["balanced"]:
                    print("(Balance check: re-planned with a softer coverage discount.)")
                print_route(route, "Route")
            elif a.cmd == "missed":
                res = missed(conn, date, a.team, a.at, parse_hm(a.time))
                print(res["message"])
                if res["replanned"]:
                    print_route(res["route"], "Route")
            elif a.cmd == "show":
                for route in routes_for_day(conn, date, a.team):
                    print_route(route, "Route")
            elif a.cmd == "compare":
                route = compare_shift(conn, a.shift)
                print_route(route, "Existing shift")
                for sk in route["skipped"]:
                    print(f"  Left out (no real train in this timetable): {sk}")
            elif a.cmd == "reset":
                reset_day(conn, date)
                print("Cleared.")
    except RouteError as err:
        sys.exit(str(err))


if __name__ == "__main__":
    main()
