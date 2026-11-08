"""
BART PES route engine, running on the real data in PostgreSQL (RDS).

Each clock-in gets the highest-scoring route for that team, given every route already handed
out that day. A missed train re-plans only that team. Routes are saved in the database.

Connection: set the usual PostgreSQL environment variables first, e.g.
    export PGHOST=bart-rds.xxxx.us-east-1.rds.amazonaws.com PGUSER=BART_MASTER PGDATABASE=bart PGSSLMODE=require
    export PGPASSWORD='...'          (or leave it out and psycopg reads ~/.pgpass)

Commands:
    python3 route_engine.py clock-in --date 2026-11-02 --team "Julio+Eric" \
        --start "West Oakland" --end "West Oakland" --from 14:10 --to 20:58
    python3 route_engine.py missed --date 2026-11-02 --team "Julio+Eric" --at "Bay Fair" --time 17:15
    python3 route_engine.py show --date 2026-11-02 [--team "Julio+Eric"]
    python3 route_engine.py compare --shift 314          (score an existing shift the same way)
    python3 route_engine.py reset --date 2026-11-02      (delete that day's routes)

Needs: pip install "psycopg[binary]"
"""
import argparse, bisect, datetime as dt, heapq, sys
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


def hm(m):
    return f"{m // 60:02d}:{m % 60:02d}"


def parse_hm(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


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
        c = self.lookup.get(name.strip().lower())
        if not c:
            sys.exit(f"Unknown station: {name!r}")
        return c

    def next_event(self, station, t):
        ev = self.events.get(station, [])
        i = bisect.bisect_left(ev, t)
        return ev[i] if i < len(ev) else None


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


# ---------- database actions ----------
def save(conn, date, team, start_code, end_code, t0, t1, legs, first_leg_no=1, clock_in=True):
    cur = conn.cursor()
    rows = []
    n = first_leg_no
    if clock_in:
        rows.append((date, team, 0, "clock_in", start_code, start_code, t0, t0, None, None, None))
    for leg in legs:
        if leg[0] == "ride":
            _, trip, a, ta, b, tb, pts = leg
            rows.append((date, team, n, "ride", a, b, ta, tb, trip, NET.trip_dir[trip][1], pts))
        else:
            _, st, ta, tb = leg
            rows.append((date, team, n, "break", st, st, ta, tb, None, None, 0))
        n += 1
    rows.append((date, team, 99, "clock_out", end_code, end_code, t1, t1, None, None, None))
    cur.executemany("INSERT INTO team_routes (date, team, leg, kind, from_code, to_code, depart_min, arrive_min, "
                    "trip_id, direction, points) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows)


def print_route(net, team, legs, t0, t1, start_code, end_code, title, start_label="clock in at"):
    rides = [l for l in legs if l[0] == "ride"]
    onboard = sum(l[5] - l[3] for l in rides)
    print(f"\n{title}: {team}")
    print(f"  {'Leg':>3}  {'Board':<24} {'Exit at':<24} {'Depart':>6} {'Arrive':>6}  {'Line':<20} Points")
    print(f"  {0:>3}  {'IN':<24} {'':<24} {hm(t0):>6} {hm(t0):>6}  {start_label} {net.names[start_code]}")
    for n, l in enumerate(legs, 1):
        if l[0] == "ride":
            _, trip, a, ta, b, tb, pts = l
            print(f"  {n:>3}  {net.names[a][:24]:<24} {net.names[b][:24]:<24} {hm(ta):>6} {hm(tb):>6}  "
                  f"{net.trip_dir[trip][1]:<20} {pts:6.1f}")
        else:
            _, st, ta, tb = l
            print(f"  {n:>3}  {'':<24} {'':<24} {hm(ta):>6} {hm(tb):>6}  {tb - ta}-minute break at {net.names[st]}")
    print(f"  {99:>3}  {'OUT':<24} {'':<24} {hm(t1):>6} {hm(t1):>6}  clock out at {net.names[end_code]}")
    span = t1 - t0
    lines = sorted({net.trip_dir[l[1]][0] for l in rides})
    gaps = [b[3] - a[5] for a, b in zip(rides, rides[1:])]
    print(f"  On board {onboard} of {span} min ({100 * onboard / span:.0f}%), {len(rides)} rides, "
          f"lines: {', '.join(lines)}, points {sum(l[6] for l in rides):.1f}"
          + (f", shortest transfer {min(gaps)} min" if gaps else ""))
    return onboard, span


def lock(conn):
    conn.execute("LOCK TABLE team_routes IN SHARE ROW EXCLUSIVE MODE")   # one clock-in at a time


def cmd_clock_in(conn, a):
    date = dt.date.fromisoformat(a.date)
    net = network(conn, date)
    s, e = net.code(a.start), net.code(a.end)
    t0, t1 = parse_hm(a.__dict__["from"]), parse_hm(a.to)
    with conn.transaction():
        lock(conn)
        conn.execute("DELETE FROM team_routes WHERE date = %s AND team = %s", (date, a.team))
        taken, coverage = load_state(conn, date)
        res = best_route(net, a.team, s, t0 + CLOCK_IN, e, t1 - CLOCK_OUT, taken, coverage, shift_start=t0)
        if res is None:
            sys.exit("No valid route for that window (too short, or every train is taken).")
        avg = [r[0] for r in conn.execute(
            "SELECT sum(points) FROM team_routes WHERE date = %s AND kind = 'ride' AND status = 'planned' "
            "GROUP BY team", (date,)).fetchall()]
        if avg and res[0] < 0.75 * float(sum(avg)) / len(avg):     # balance check
            softer = best_route(net, a.team, s, t0 + CLOCK_IN, e, t1 - CLOCK_OUT, taken, coverage,
                                shift_start=t0, discount=0.75)
            if softer and softer[0] > res[0]:
                res = softer
                print("(Balance check: re-planned with a softer coverage discount.)")
        conn.execute("INSERT INTO team_requests VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (date, team) DO UPDATE "
                     "SET start_code=EXCLUDED.start_code, end_code=EXCLUDED.end_code, from_min=EXCLUDED.from_min, "
                     "to_min=EXCLUDED.to_min", (date, a.team, s, e, t0, t1))
        save(conn, date, a.team, s, e, t0, t1, res[1])
    print_route(net, a.team, res[1], t0, t1, s, e, "Route")


def cmd_missed(conn, a):
    date = dt.date.fromisoformat(a.date)
    net = network(conn, date)
    here, now = net.code(a.at), parse_hm(a.time)
    with conn.transaction():
        lock(conn)
        req = conn.execute("SELECT start_code, end_code, from_min, to_min FROM team_requests "
                           "WHERE date = %s AND team = %s", (date, a.team)).fetchone()
        if not req:
            sys.exit("That team has not clocked in today.")
        s, e, t0, t1 = req
        breaks_done = conn.execute("SELECT count(*) FROM team_routes WHERE date=%s AND team=%s AND kind='break' "
                                   "AND status='planned' AND arrive_min <= %s", (date, a.team, now)).fetchone()[0]
        # keep what was ridden, drop this team's future legs; everyone else stays locked
        conn.execute("UPDATE team_routes SET status='replaced' WHERE date=%s AND team=%s AND status='planned' "
                     "AND (depart_min >= %s OR kind='clock_out')", (date, a.team, now))
        taken, coverage = load_state(conn, date)
        res = best_route(net, a.team, here, now, e, t1 - CLOCK_OUT, taken, coverage,
                         breaks_done=breaks_done, shift_start=t0)
        if res is None:
            conn.execute("UPDATE team_routes SET status='planned' WHERE date=%s AND team=%s AND kind='clock_out'",
                         (date, a.team))
            print(f"No way to finish the planned route from {net.names[here]}; head to {net.names[e]} directly.")
            return
        last = conn.execute("SELECT coalesce(max(leg), 0) FROM team_routes WHERE date=%s AND team=%s AND "
                            "status='planned' AND leg < 99", (date, a.team)).fetchone()[0]
        save(conn, date, a.team, here, e, now, t1, res[1], first_leg_no=last + 1, clock_in=False)
    print_route(net, a.team, res[1], now, t1, here, e, f"New route from {net.names[here]} {hm(now)}",
                start_label="re-planned from")


def cmd_show(conn, a):
    date = dt.date.fromisoformat(a.date)
    net = network(conn, date)
    q = ("SELECT team, kind, from_code, to_code, depart_min, arrive_min, trip_id, points FROM team_routes "
         "WHERE date=%s AND status='planned'" + (" AND team=%s" if a.team else "") + " ORDER BY team, leg")
    rows = conn.execute(q, (date, a.team) if a.team else (date,)).fetchall()
    by = defaultdict(list)
    for r in rows:
        by[r[0]].append(r)
    for team, lst in by.items():
        t0, t1 = lst[0][4], lst[-1][4]
        legs = [("ride", r[6], r[2], r[4], r[3], r[5], float(r[7] or 0)) if r[1] == "ride" else
                ("break", r[2], r[4], r[5]) for r in lst if r[1] in ("ride", "break")]
        print_route(net, team, legs, t0, t1, lst[0][2], lst[-1][2], "Route")


def cmd_compare(conn, a):
    """Score an existing shift with the same rules, to compare with the engine."""
    row = conn.execute("SELECT day_type FROM shift_library WHERE shift=%s", (a.shift,)).fetchone()
    if not row:
        sys.exit("Unknown shift.")
    net = Network(conn, row[0])
    legs, skipped, t0, t1, start, end = [], [], None, None, None, None
    for kind, frm, to, dep, arr, trip in conn.execute(
            "SELECT kind, from_code, to_code, depart, arrive, matched_trip_id FROM shift_legs "
            "WHERE shift=%s ORDER BY leg", (a.shift,)).fetchall():
        if kind == "clock_in" and dep:
            t0 = parse_hm(dep)
        elif kind == "clock_out" and dep:
            t1 = parse_hm(dep)
        elif kind == "ride" and dep and arr:
            ta, tb = parse_hm(dep), parse_hm(arr)
            pts = 0.0
            if trip and trip in net.trips:
                stops = net.trips[trip]
                i = next(k for k, (c, t) in enumerate(stops) if c == frm and t == ta)
                for j in range(i + 1, len(stops)):
                    pts += (stops[j][1] - stops[j - 1][1]) * net.seg_weight[(trip, j - 1)]
                    if stops[j][0] == to:
                        break
            start = start or frm
            end = to
            if trip and trip in net.trips:
                legs.append(("ride", trip, frm, ta, to, tb, round(pts, 2)))
            else:
                skipped.append(f"{net.names.get(frm, frm)} {dep} to {net.names.get(to, to)} {arr}")
    print_route(net, f"shift {a.shift}", legs, t0, t1, start, end, "Existing shift")
    for sk in skipped:
        print(f"  Left out (no real train in this timetable): {sk}")


NET = None


def network(conn, date):
    global NET
    NET = Network(conn, "weekend" if date.weekday() >= 5 else "weekday")
    return NET


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
    with psycopg.connect(autocommit=True) as conn:
        conn.execute(ROUTES_DDL)
        if a.cmd == "clock-in":
            cmd_clock_in(conn, a)
        elif a.cmd == "missed":
            cmd_missed(conn, a)
        elif a.cmd == "show":
            cmd_show(conn, a)
        elif a.cmd == "compare":
            cmd_compare(conn, a)
        elif a.cmd == "reset":
            conn.execute("DELETE FROM team_routes WHERE date=%s", (dt.date.fromisoformat(a.date),))
            conn.execute("DELETE FROM team_requests WHERE date=%s", (dt.date.fromisoformat(a.date),))
            print("Cleared.")


if __name__ == "__main__":
    main()
