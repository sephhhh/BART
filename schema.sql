-- BART PES database: 12 tables built by build_tables.py
-- Safe to rerun: drops and recreates everything.

DROP TABLE IF EXISTS events, assignments, surveyors, shift_legs, shift_library, congestion,
    station_zones, dayparts, ride_edges, stop_times_weekend, stop_times_weekday, stations CASCADE;

CREATE TABLE stations (
    code          text PRIMARY KEY,          -- BART platform code, e.g. K20
    name          text NOT NULL,
    ff_number     int,                       -- FastField station number
    ff_initials   text,
    ff_name       text,
    used_as_home  int                        -- how many existing shifts start here
);

CREATE TABLE stop_times_weekday (
    day_type      text NOT NULL,
    route         int  NOT NULL,             -- 1/2 Yellow, 3/4 Orange, 5/6 Green, 7/8 Red, 11/12 Blue
    line          text NOT NULL,
    direction     text NOT NULL,             -- e.g. 'Yellow (to AN)'
    trip_id       text NOT NULL,             -- one train trip, e.g. @3610606
    train_id      text NOT NULL,             -- the physical train, reused across trips
    stop_seq      int  NOT NULL,
    station_code  text NOT NULL REFERENCES stations(code),
    time_min      int  NOT NULL,             -- minutes after midnight; past midnight goes above 1440
    time          text NOT NULL,
    PRIMARY KEY (trip_id, stop_seq)
);
CREATE TABLE stop_times_weekend (LIKE stop_times_weekday INCLUDING ALL);
ALTER TABLE stop_times_weekend ADD FOREIGN KEY (station_code) REFERENCES stations(code);

CREATE TABLE ride_edges (                  -- every "board trip at A, get off at B"
    day_type      text NOT NULL,
    trip_id       text NOT NULL,
    route         int  NOT NULL,
    direction     text NOT NULL,
    from_code     text NOT NULL REFERENCES stations(code),
    depart_min    int  NOT NULL,
    to_code       text NOT NULL REFERENCES stations(code),
    arrive_min    int  NOT NULL,
    minutes       int  NOT NULL
);

CREATE TABLE dayparts (
    daypart       int PRIMARY KEY,
    day_type      text NOT NULL,
    name          text NOT NULL,
    start_min     int NOT NULL,
    end_min       int NOT NULL
);

CREATE TABLE station_zones (
    line          text NOT NULL,
    station_code  text NOT NULL REFERENCES stations(code),
    station_name  text,
    zone          int  NOT NULL,
    zone_name     text
);

CREATE TABLE congestion (
    route         int  NOT NULL,
    direction     text NOT NULL,
    zone          int  NOT NULL,
    daypart       int  NOT NULL,
    in_out_core   text,
    congestion    text NOT NULL,             -- Open / Constrained / Restricted
    weight        numeric NOT NULL,          -- points multiplier, tunable
    PRIMARY KEY (direction, zone, daypart)
);

CREATE TABLE shift_library (
    shift            int PRIMARY KEY,
    day_type         text,
    day_type_source  text,                   -- 'sheet' or 'inferred'
    rides            int
);

CREATE TABLE shift_legs (
    shift            int NOT NULL REFERENCES shift_library(shift),
    leg              int,
    day_type         text,
    kind             text NOT NULL,          -- clock_in / ride / clock_out / other
    from_raw         text,
    from_code        text REFERENCES stations(code),
    to_raw           text,
    to_code          text REFERENCES stations(code),
    depart           text,
    arrive           text,
    minutes          int,
    direction        text,
    matched_trip_id  text,                   -- the real train this leg rides, if found
    gap_before_min   int,
    note             text,
    issues           text
);

CREATE TABLE surveyors (
    name          text PRIMARY KEY,
    survey_code   text,
    status        text
);

CREATE TABLE assignments (
    date               date NOT NULL,
    surveyor           text NOT NULL,
    shift_or_activity  text NOT NULL,
    kind               text NOT NULL,        -- route shift / unknown shift / other activity
    comment            text
);

CREATE TABLE events (
    date          date NOT NULL,
    sports        text,
    other_event   text,
    location      text
);
