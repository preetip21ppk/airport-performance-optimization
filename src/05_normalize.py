from __future__ import annotations

import os

import pandas as pd

from common import CFG, curated_path, flush_audit, qa_log

CURATED = CFG["paths"]["curated"]

def main() -> None:
    print("=== STAGE 4: NORMALIZE STAGING INTO ENTITIES ===\n")
    staging = pd.read_parquet(os.path.join(CURATED, "staging.parquet"))
    airport_clean = pd.read_parquet(os.path.join(CURATED, "airport_clean.parquet"))
    weather_clean = pd.read_parquet(os.path.join(CURATED, "weather_clean.parquet"))
    aircraft_clean = pd.read_parquet(os.path.join(CURATED, "aircraft_clean.parquet"))
    qa_log("normalize", "staging_rows", len(staging))

    in_scope = pd.Index(sorted(set(staging["origin"].dropna())
                               | set(staging["dest"].dropna())), name="airport_code")
    airport = (airport_clean[airport_clean["airport_code"].isin(in_scope)]
               .sort_values("airport_code").reset_index(drop=True))
    airport.insert(0, "airport_id", airport.index + 1)

    airport = airport[["airport_id", "airport_code", "airport_name", "city", "state"]]
    qa_log("normalize", "Airport_rows", len(airport))
    ap_id = airport.set_index("airport_code")["airport_id"]

    airline = (staging[["airline_code", "airline_name"]]
               .dropna(subset=["airline_code"]).drop_duplicates()
               .sort_values("airline_code").reset_index(drop=True))
    airline.insert(0, "airline_id", airline.index + 1)
    qa_log("normalize", "Airline_rows", len(airline))
    al_id = airline.set_index("airline_code")["airline_id"]

    tails = staging.loc[staging["tail_number"].notna(), ["tail_number", "airline_code"]]
    operator = (tails.groupby(["tail_number", "airline_code"]).size()
                .rename("flights").reset_index()
                .sort_values(["tail_number", "flights"], ascending=[True, False])
                .drop_duplicates("tail_number")[["tail_number", "airline_code"]])
    multi = tails.groupby("tail_number")["airline_code"].nunique()
    qa_log("normalize", "tails_operated_by_multiple_carriers",
           int((multi > 1).sum()), "assigned to the carrier with the most flights")

    aircraft = (aircraft_clean[aircraft_clean["tail_number"].isin(operator["tail_number"])]
                .drop_duplicates("tail_number")
                .merge(operator, on="tail_number", how="right")
                .sort_values("tail_number").reset_index(drop=True))
    aircraft["airline_id"] = aircraft["airline_code"].map(al_id).astype("Int64")
    aircraft.insert(0, "aircraft_id", aircraft.index + 1)
    aircraft = aircraft[["aircraft_id", "tail_number", "manufacturer", "model",
                         "airline_id"]]
    qa_log("normalize", "Aircraft_rows", len(aircraft))
    qa_log("normalize", "Aircraft_without_faa_model",
           int(aircraft["model"].isna().sum()))
    ac_id = aircraft.set_index("tail_number")["aircraft_id"]

    weather = weather_clean[weather_clean["airport_code"].isin(in_scope)].copy()
    weather["airport_id"] = weather["airport_code"].map(ap_id).astype("Int64")
    weather = (weather.dropna(subset=["airport_id"])
               .sort_values(["airport_code", "observed_at_utc"]).reset_index(drop=True))
    weather.insert(0, "weather_id", weather.index + 1)

    weather = weather[["weather_id", "airport_id", "airport_code", "observed_at_utc",
                       "temperature_f", "visibility_mi"]]
    qa_log("normalize", "Weather_rows", len(weather))

    wx_key = weather.set_index(["airport_code", "observed_at_utc"])["weather_id"]
    idx = pd.MultiIndex.from_arrays([staging["origin"],
                                     staging["weather_observed_at_utc"]])
    staging["weather_id"] = wx_key.reindex(idx).to_numpy()
    staging["weather_id"] = pd.array(staging["weather_id"], dtype="Int64")
    qa_log("normalize", "flights_with_weather_id",
           int(staging["weather_id"].notna().sum()),
           f"{100*staging['weather_id'].notna().mean():.2f}% of flights")

    flight = pd.DataFrame({
        "airline_id": staging["airline_code"].map(al_id).astype("Int64"),
        "origin_airport_id": staging["origin"].map(ap_id).astype("Int64"),
        "dest_airport_id": staging["dest"].map(ap_id).astype("Int64"),
        "aircraft_id": staging["tail_number"].map(ac_id).astype("Int64"),
        "weather_id": staging["weather_id"],
        "flight_number": staging["flight_number"],
        "service_date": staging["service_date"],
        "delay_minutes": staging["delay_minutes"],
        "is_cancelled": staging["is_cancelled"],
        "is_diverted": staging["is_diverted"],
        "is_analytical": staging["is_analytical"],
        "is_on_time": staging["is_on_time"],
        "carrier_delay_min": staging["carrier_delay_min"],
        "weather_delay_min": staging["weather_delay_min"],
        "nas_delay_min": staging["nas_delay_min"],
        "security_delay_min": staging["security_delay_min"],
        "late_aircraft_delay_min": staging["late_aircraft_delay_min"],
    }).reset_index(drop=True)
    flight.insert(0, "flight_id", flight.index + 1)
    qa_log("normalize", "Flight_rows", len(flight))

    for col, parent, parent_key in [
        ("origin_airport_id", airport, "airport_id"),
        ("dest_airport_id", airport, "airport_id"),
        ("airline_id", airline, "airline_id"),
        ("aircraft_id", aircraft, "aircraft_id"),
        ("weather_id", weather, "weather_id"),
    ]:
        vals = flight[col].dropna().unique()
        orphans = len(set(vals) - set(parent[parent_key]))
        qa_log("referential", f"Flight.{col}_orphans", orphans,
               "must be 0" if orphans else "OK")
        qa_log("referential", f"Flight.{col}_null",
               int(flight[col].isna().sum()),
               f"{100*flight[col].isna().mean():.2f}% (nullable FK)"
               if flight[col].isna().any() else "")

    k = CFG["kpi"]
    kpi = pd.DataFrame([
        {"kpi_id": 1, "metric_name": "OnTimePerformance",
         "threshold_minutes": k["on_time_threshold_minutes"],
         "target_pct": k["target_on_time_pct"]},
    ])
    qa_log("normalize", "KPIThreshold_rows", len(kpi))

    for name, df in [("Airport", airport), ("Airline", airline),
                     ("Aircraft", aircraft), ("Weather", weather),
                     ("Flight", flight), ("KPIThreshold", kpi)]:
        path = curated_path(f"entity_{name}.parquet")
        df.to_parquet(path, index=False)
        print(f"  {name:<14} {len(df):>10,} rows -> {os.path.basename(path)}")

    flush_audit("qa_stage4_normalize")

if __name__ == "__main__":
    main()
