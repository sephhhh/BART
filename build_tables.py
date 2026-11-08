"""
BART PES data pipeline: turn the five raw workbooks (as landed in S3) into clean CSV tables
ready to load into PostgreSQL on RDS.

Usage:  python3 build_tables.py <folder with the 5 xlsx files> <output folder>
Needs:  pip install openpyxl
"""
import csv, datetime as dt, os, re, sys, warnings
from collections import Counter, defaultdict
import openpyxl

warnings.filterwarnings("ignore")
SRC, OUT = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)

FILES = {
    "weekday": "Weekday-August-2026.xlsx",
    "weekend": "Weekend-August-2026.xlsx",
    "fastfield": "Copy_of_2026_FastField_Inputs.xlsx",
    "team": "Copy_of_Team_Views.xlsx",
    "viewer": "Copy_of_Shift_Viewer_.xlsx",
}
ROUTES = {  # timetable route number -> (line, direction label used in the shift library)
    1: ("Yellow", "Yellow (to SFO/MB)"), 2: ("Yellow", "Yellow (to AN)"),
    3: ("Orange", "Orange (to RM)"), 4: ("Orange", "Orange (to Berry)"),
    5: ("Green", "Green (to DC)"), 6: ("Green", "Green (to Berry)"),
    7: ("Red", "Red (to SFO/MB)"), 8: ("Red", "Red (to RM)"),
    11: ("Blue", "Blue (to DC)"), 12: ("Blue", "Blue (to Dub)"),
}
CONGESTION_WEIGHT = {"Open": 1.0, "Constrained": 0.5, "Restricted": 0.0}  # starting values, tunable

# Every spelling seen in the files -> BART platform code used by the timetables.
EXTRA_ALIASES = {
    "12th. st.": "K10", "12th street": "K10", "16th. st.": "M50", "16th street": "M50", "19th. st.": "K20",
    "19th street": "K20", "19th": "K20", "24th. st.": "M60", "24th street": "M60", "24th": "M60",
    "balboa": "M80", "berryessa": "S50", "civic center": "M40", "del norte": "R50", "dublin": "L30",
    "dwntn berkeley": "R20", "emb": "M16", "montgomery": "M20", "north concord": "C70", "pittsburg/bp": "C80",
    "pleasant hill": "C50", "powell": "M30", "s. hayward": "A70", "s. san francisco": "W20", "sfo": "Y10",
    "warm springs": "S20", "west dublin": "L20", "west oakland station": "M10", "w oak": "M10", "w. oak": "M10",
    "daily city": "M90", "milpitas": "S40", "el cerrito": "R40",
}
NOT_STATIONS = {"in", "out", "0ut", "bhq", "placeholder shift", "oak", "none", ""}


def norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def minutes(v):
    """Excel time / timedelta (after midnight) / datetime -> minutes after the service day starts."""
    if isinstance(v, dt.timedelta):
        return round(v.total_seconds() / 60)
    if isinstance(v, dt.datetime):  # 1900-01-01 hh:mm means the next day in these files
        return (1440 if v.year == 1900 and v.day == 1 else 0) + v.hour * 60 + v.minute
    if isinstance(v, dt.time):
        return v.hour * 60 + v.minute
    return None


def hhmm(m):
    return None if m is None else f"{m // 60:02d}:{m % 60:02d}"


def write(name, header, rows):
    with open(os.path.join(OUT, name), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print(f"  {name}: {len(rows)} rows")


def book(key, read_only=False):
    return openpyxl.load_workbook(os.path.join(SRC, FILES[key]), data_only=True, read_only=read_only)


print("Reading workbooks...")
wd, we, ff = book("weekday", True), book("weekend", True), book("fastfield")
tv, sv = book("team"), book("viewer")

# ---------- 1. stations ----------
tt_names = {r[0]: r[1] for r in wd["Lookup"].iter_rows(min_row=2, values_only=True) if r[0]}
alias = {norm(n): c for c, n in tt_names.items()}
alias.update(EXTRA_ALIASES)
ff_codes = [r for r in ff["Stations and Codes"].iter_rows(min_row=2, values_only=True) if r[0]]
by_code = {c: {"name": n, "ff_number": None, "ff_initials": None, "ff_name": None} for c, n in tt_names.items()}
unmapped_ff = []
for num, ini, name in ff_codes:
    c = alias.get(norm(name))
    if c:
        by_code[c].update(ff_number=int(num), ff_initials=str(ini), ff_name=name)
    else:
        unmapped_ff.append(name)


def station(name):
    n = norm(name)
    if n in NOT_STATIONS:
        return None
    return alias.get(n)


home = Counter(norm(r[4]) for r in ff["PESShifts"].iter_rows(min_row=2, values_only=True) if r[0])
write("stations.csv", ["code", "name", "ff_number", "ff_initials", "ff_name", "used_as_home"],
      [[c, v["name"], v["ff_number"], v["ff_initials"], v["ff_name"],
        sum(k for a, k in home.items() if alias.get(a) == c)] for c, v in sorted(by_code.items())])

# ---------- 2. timetable stop times ----------
trips = {}
for day, wb in (("weekday", wd), ("weekend", we)):
    rows, seq = [], Counter()
    for r in wb["Data"].iter_rows(min_row=2, values_only=True):
        if r[0] is None:
            continue
        route, train, trip, code, t = int(r[0]), r[1], r[2], r[3], minutes(r[4])
        seq[trip] += 1
        line, direction = ROUTES[route]
        rows.append([day, route, line, direction, trip, train, seq[trip], code, t, hhmm(t)])
        trips.setdefault((day, trip), []).append((code, t, route))
    write(f"stop_times_{day}.csv",
          ["day_type", "route", "line", "direction", "trip_id", "train_id", "stop_seq", "station_code",
           "time_min", "time"], rows)

# ---------- 3. ride edges (every ordered pair of stops on a trip) ----------
edges = []
for (day, trip), stops in trips.items():
    for i in range(len(stops)):
        for j in range(i + 1, len(stops)):
            (a, ta, route), (b, tb, _) = stops[i], stops[j]
            edges.append([day, trip, route, ROUTES[route][1], a, ta, b, tb, tb - ta])
write("ride_edges.csv", ["day_type", "trip_id", "route", "direction", "from_code", "depart_min", "to_code",
                         "arrive_min", "minutes"], edges)

# ---------- 4. dayparts, zones, congestion ----------
dayparts = []
for r in ff["Dayparts"].iter_rows(min_row=2, values_only=True):
    if r[0] in ("Weekday", "Weekend"):
        dayparts.append([int(r[3]), r[0].lower(), r[4], minutes(r[1]), minutes(r[2])])
write("dayparts.csv", ["daypart", "day_type", "name", "start_min", "end_min"], dayparts)

zones, zone_miss = [], []
for r in ff["Zones"].iter_rows(min_row=2, values_only=True):
    if not r[0]:
        continue
    c = station(r[3])
    (zones if c else zone_miss).append([r[1], c, r[3], int(r[4]), r[5]])
write("station_zones.csv", ["line", "station_code", "station_name", "zone", "zone_name"], zones)

cong = []
for r in ff["Coverage Lookup"].iter_rows(min_row=2, values_only=True):
    if r[0]:
        route = next(k for k, v in ROUTES.items() if v[1] == r[0])
        cong.append([route, r[0], int(r[1]), int(r[2]), r[3], r[4], CONGESTION_WEIGHT[r[4]]])
write("congestion.csv", ["route", "direction", "zone", "daypart", "in_out_core", "congestion", "weight"], cong)

# ---------- 5. existing shift library ----------
legs_raw = [r for r in ff["Q4Shift Detail"].iter_rows(min_row=2, values_only=True) if r[0] is not None]
cov_day = defaultdict(Counter)
for r in ff["Shift Coverage"].iter_rows(min_row=2, values_only=True):
    if r[0] is not None and r[5] in ("Weekday", "Weekend"):
        cov_day[int(float(r[0]))][r[5].lower()] += 1
stop_index = defaultdict(list)
for (day, trip), stops in trips.items():
    for i, (c, t, route) in enumerate(stops):
        stop_index[(day, c, t)].append((trip, i))


def match(day, fc, tc, dep, arr):
    for trip, i in stop_index.get((day, fc, dep), []):
        for c, t, route in trips[(day, trip)][i + 1:]:
            if c == tc:
                if t == arr:
                    return trip
                break
    return None


by_shift = defaultdict(list)
for r in legs_raw:
    by_shift[int(float(r[0]))].append(r)
shift_rows, leg_rows = [], []
for s, lst in sorted(by_shift.items()):
    day = cov_day[s].most_common(1)[0][0] if cov_day[s] else None
    source = "sheet" if day else None
    if day is None:  # infer from whichever timetable the rides match
        score = {d: sum(1 for r in lst if station(r[2]) and station(r[3]) and minutes(r[4]) is not None
                        and match(d, station(r[2]), station(r[3]), minutes(r[4]), minutes(r[5])))
                 for d in ("weekday", "weekend")}
        if any(score.values()):
            day, source = max(score, key=score.get), "inferred"
    prev = None
    for r in lst:
        frm, to, dep, arr = station(r[2]), station(r[3]), minutes(r[4]), minutes(r[5])
        note = str(r[8]).strip() if r[8] else ""
        kind = ("clock_in" if norm(r[2]) == "in" else "clock_out" if norm(r[2]) in ("out", "0ut") or r[1] == 99
                else "ride" if frm and to else "other")
        trip = match(day, frm, to, dep, arr) if kind == "ride" and day and dep is not None else None
        gap = dep - prev[1] if kind == "ride" and prev and dep is not None and prev[1] is not None else None
        issue = []
        if not isinstance(r[1], (int, float)):
            issue.append("missing leg number")
        if kind == "ride" and day and trip is None:
            issue.append("no train in Aug 2026 timetable")
        if gap is not None and gap < 0:
            issue.append("departs before previous arrival")
        elif gap is not None and gap < 5 and prev[0] != trip:
            issue.append(f"{gap}-min transfer")
        leg_rows.append([s, int(r[1]) if isinstance(r[1], (int, float)) else None, day, kind, r[2], frm, r[3], to, hhmm(dep), hhmm(arr),
                         None if dep is None or arr is None else arr - dep, r[9], trip, gap, note, "; ".join(issue)])
        if kind == "ride":
            prev = (trip, arr)
    shift_rows.append([s, day, source, len([l for l in lst if station(l[2]) and station(l[3])])])
write("shift_library.csv", ["shift", "day_type", "day_type_source", "rides"], shift_rows)
write("shift_legs.csv", ["shift", "leg", "day_type", "kind", "from_raw", "from_code", "to_raw", "to_code",
                         "depart", "arrive", "minutes", "direction", "matched_trip_id", "gap_before_min",
                         "note", "issues"], leg_rows)

# ---------- 6. surveyors, assignments, events ----------
people = [r for r in ff["SurveyTakers"].iter_rows(min_row=2, values_only=True) if r[0]]
write("surveyors.csv", ["name", "survey_code", "status"], [[p[0], p[5], p[4]] for p in people])

assign = []
known = set(by_shift)
for name in ("Julio", "Eric", "Anthony", "James"):
    for r in tv[name].iter_rows(min_row=2, values_only=True):
        if not isinstance(r[0], dt.datetime) or r[2] in (None, ""):
            continue
        v = r[2]
        if isinstance(v, (int, float)):
            kind = "route shift" if int(v) in known else "unknown shift"
            v = int(v)
        else:
            kind = "other activity"
        assign.append([r[0].date().isoformat(), name, v, kind, r[6] or ""])
write("assignments.csv", ["date", "surveyor", "shift_or_activity", "kind", "comment"], assign)

events = []
for r in sv["EventTable"].iter_rows(min_row=3, values_only=True):
    if isinstance(r[1], dt.datetime) and (r[2] or r[3]):
        events.append([r[1].date().isoformat(), r[2] or "", r[3] or "", r[4] or ""])
write("events.csv", ["date", "sports", "other_event", "location"], events)

print("\nUnmapped FastField station names:", unmapped_ff)
print("Zone rows with unmapped station:", [z[2] for z in zone_miss])
