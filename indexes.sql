-- Indexes for the lookups the route engine makes. Run after loading (faster than before).
CREATE INDEX ON ride_edges (day_type, from_code, depart_min);
CREATE INDEX ON stop_times_weekday (station_code, time_min);
CREATE INDEX ON stop_times_weekend (station_code, time_min);
CREATE INDEX ON shift_legs (shift);
CREATE INDEX ON assignments (date);
ANALYZE;
