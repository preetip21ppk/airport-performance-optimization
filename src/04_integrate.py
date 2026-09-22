from __future__ import annotations

import os

import pandas as pd

from common import (CFG, curated_path, flush_audit, merge_audit, qa_log)

CURATED = CFG["paths"]["curated"]
WEATHER_TOLERANCE = pd.Timedelta(minutes=90)

def read(name: str, **kw) -> pd.DataFrame:
    return pd.read_parquet(os.path.join(CURATED, name), **kw)

def main() -> None:
    print("=== STAGE 3: INTEGRATE INTO STAGING ===\n")

    flight = read("flight_clean.parquet")
    airport = read("airport_clean.parquet")
    airline = read("airline_clean.parquet")
    aircraft = read("aircraft_clean.parquet")
    weather = read("weather_clean.parquet")

    qa_log("integrate", "flight_rows_in", len(flight))
    base_rows = len(flight)

    origin_cols = airport.rename(columns={
        "airport_code": "origin", "airport_name": "origin_airport_name",
        "city": "origin_city", "state": "origin_state",
        "latitude": "origin_lat", "longitude": "origin_lon",
        "airport_type": "origin_airport_type",
    })[["origin", "origin_airport_name", "origin_city", "origin_state",
        "origin_lat", "origin_lon", "origin_airport_type"]]
    before = len(flight)
    flight = flight.merge(origin_cols, on="origin", how="left", validate="m:1")
    merge_audit("flight->airport(origin)", before, flight, "origin_airport_name")

    dest_cols = airport.rename(columns={
        "airport_code": "dest", "airport_name": "dest_airport_name",
        "city": "dest_city", "state": "dest_state",
    })[["dest", "dest_airport_name", "dest_city", "dest_state"]]
    before = len(flight)
    flight = flight.merge(dest_cols, on="dest", how="left", validate="m:1")
    merge_audit("flight->airport(dest)", before, flight, "dest_airport_name")

    before = len(flight)
    flight = flight.merge(airline, on="airline_code", how="left", validate="m:1")
    merge_audit("flight->airline", before, flight, "airline_name")

    ac = aircraft[["tail_number", "manufacturer", "model", "certificated_max_seats",
                   "n_engines", "year_mfr", "weight_class"]]
    before = len(flight)
    flight = flight.merge(ac, on="tail_number", how="left", validate="m:1")
    merge_audit("flight->aircraft", before, flight, "model")
    has_tail = flight["tail_number"].notna()
    qa_log("flight->aircraft", "match_rate_where_tail_present",
           f"{100*flight.loc[has_tail, 'model'].notna().mean():.2f}%")

    wx = (weather[["airport_code", "observed_at_utc", "temperature_f",
                   "humidity_pct", "visibility_mi", "wind_speed_kt", "sky_cover"]]
          .rename(columns={"airport_code": "origin"})
          .sort_values("observed_at_utc")
          .reset_index(drop=True))
    wx["weather_observed_at_utc"] = wx["observed_at_utc"]

    wx["origin"] = wx["origin"].astype(str)

    matchable = flight["scheduled_dep_utc"].notna()
    qa_log("flight->weather", "rows_with_utc_departure", int(matchable.sum()))

    left = (flight.loc[matchable, ["origin", "scheduled_dep_utc"]]
            .rename_axis("flight_row").reset_index()
            .sort_values("scheduled_dep_utc").reset_index(drop=True))
    left["origin"] = left["origin"].astype(str)

    merged = pd.merge_asof(
        left, wx,
        left_on="scheduled_dep_utc", right_on="observed_at_utc",
        by="origin", direction="nearest", tolerance=WEATHER_TOLERANCE,
    )
    matched = merged["weather_observed_at_utc"].notna()
    qa_log("flight->weather", "matched_within_90min", int(matched.sum()),
           f"{100*matched.mean():.2f}% of rows with a UTC departure time")
    gap = (merged.loc[matched, "scheduled_dep_utc"]
           - merged.loc[matched, "weather_observed_at_utc"]).abs()
    qa_log("flight->weather", "median_match_gap_minutes",
           f"{gap.dt.total_seconds().median()/60:.1f}")
    qa_log("flight->weather", "p99_match_gap_minutes",
           f"{gap.dt.total_seconds().quantile(0.99)/60:.1f}")

    wx_cols = ["weather_observed_at_utc", "temperature_f", "humidity_pct",
               "visibility_mi", "wind_speed_kt", "sky_cover"]
    attach = merged.set_index("flight_row")[wx_cols]
    before = len(flight)
    flight = flight.join(attach, how="left")
    merge_audit("flight->weather", before, flight, "weather_observed_at_utc")

    qa_log("integrate", "staging_rows", len(flight))
    qa_log("integrate", "staging_columns", len(flight.columns))
    assert len(flight) == base_rows, (
        f"staging row count changed: {base_rows} -> {len(flight)}; a merge inflated rows"
    )
    qa_log("integrate", "row_count_preserved", "PASS",
           f"{base_rows:,} rows in, {len(flight):,} rows out")

    for col, label in [("origin_airport_name", "origin airport"),
                       ("airline_name", "airline"),
                       ("model", "aircraft model"),
                       ("weather_observed_at_utc", "weather observation")]:
        n = int(flight[col].isna().sum())
        qa_log("integrate", f"unresolved_{label.replace(' ', '_')}", n,
               f"{100*n/len(flight):.2f}% of staging rows")

    out = curated_path("staging.parquet")
    flight.to_parquet(out, index=False)
    print(f"\nstaging written -> {out}")
    print(f"  {len(flight):,} rows x {len(flight.columns)} columns")
    flush_audit("qa_stage3_integrate")

if __name__ == "__main__":
    main()
