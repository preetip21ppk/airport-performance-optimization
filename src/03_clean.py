from __future__ import annotations

import glob
import os

import numpy as np
import pandas as pd

from common import (CFG, curated_path, flush_audit, norm_code, norm_tail,
                    parse_hhmm, qa_log)

RAW_F = CFG["paths"]["raw_flights"]
RAW_R = CFG["paths"]["raw_reference"]
RAW_W = CFG["paths"]["raw_weather"]

FLIGHT_COLS = [
    "FlightDate", "Year", "Month", "DayOfWeek",
    "Reporting_Airline", "DOT_ID_Reporting_Airline", "Tail_Number",
    "Flight_Number_Reporting_Airline",
    "Origin", "Dest",
    "CRSDepTime", "DepTime", "DepDelay",
    "CRSArrTime", "ArrTime", "ArrDelay",
    "Cancelled", "CancellationCode", "Diverted",
    "Distance", "CRSElapsedTime", "ActualElapsedTime",
    "CarrierDelay", "WeatherDelay", "NASDelay", "SecurityDelay", "LateAircraftDelay",
]

TZ_OVERRIDES = {k.upper(): v for k, v in (CFG.get("timezone_overrides") or {}).items()}

IMPLAUSIBLE_DELAY_MIN = -300
IMPLAUSIBLE_DELAY_MAX = 60 * 48

WEATHER_BOUNDS = {
    "temperature_f": (-80.0, 135.0),
    "dewpoint_f": (-100.0, 90.0),
    "humidity_pct": (0.0, 100.0),
    "visibility_mi": (0.0, 100.0),
    "wind_speed_kt": (0.0, 150.0),
    "wind_dir_deg": (0.0, 360.0),
    "wind_gust_kt": (0.0, 200.0),
}

def clean_airport() -> pd.DataFrame:
    la = pd.read_csv(os.path.join(RAW_R, "L_AIRPORT.csv"), encoding="latin-1",
                     low_memory=False)
    la["airport_code"] = norm_code(la["Code"])

    desc = la["Description"].astype("string")
    la["city_state"] = desc.str.split(":").str[0].str.strip()
    la["airport_name"] = desc.str.split(":").str[1:].str.join(":").str.strip()
    la["city"] = la["city_state"].str.rsplit(",", n=1).str[0].str.strip()
    la["state"] = la["city_state"].str.rsplit(",", n=1).str[1].str.strip()
    ap = (la[["airport_code", "airport_name", "city", "state"]]
          .dropna(subset=["airport_code"])
          .drop_duplicates(subset=["airport_code"]))
    qa_log("clean_airport", "bts_lookup_rows", len(ap))

    oa = pd.read_csv(os.path.join(RAW_R, "ourairports_airports.csv"), low_memory=False)
    oa["airport_code"] = norm_code(oa["iata_code"])
    oa = (oa.dropna(subset=["airport_code"])
            .drop_duplicates(subset=["airport_code"])
            [["airport_code", "latitude_deg", "longitude_deg", "elevation_ft",
              "iso_region", "municipality", "type"]])
    ap = ap.merge(oa, on="airport_code", how="left", validate="1:1")

    cols = ["of_id", "name", "city", "country", "iata", "icao", "lat", "lon", "alt",
            "tz_offset", "dst", "tz_name", "type", "source"]
    of = pd.read_csv(os.path.join(RAW_R, "openflights_airports.dat"), header=None,
                     names=cols, low_memory=False, na_values=["\\N", ""])
    of["airport_code"] = norm_code(of["iata"])
    of["tz_name"] = of["tz_name"].astype("string").str.strip()
    of.loc[of["tz_name"].isin(["\\N", ""]), "tz_name"] = pd.NA
    of = (of.dropna(subset=["airport_code"])
            .drop_duplicates(subset=["airport_code"])[["airport_code", "tz_name"]])
    ap = ap.merge(of, on="airport_code", how="left", validate="1:1")
    ap["tz_name"] = ap["tz_name"].fillna(ap["airport_code"].map(TZ_OVERRIDES))

    ap = ap.rename(columns={"latitude_deg": "latitude", "longitude_deg": "longitude",
                            "elevation_ft": "elevation_ft", "iso_region": "region",
                            "type": "airport_type"})
    qa_log("clean_airport", "rows_out", len(ap))
    qa_log("clean_airport", "missing_latitude", int(ap["latitude"].isna().sum()))
    qa_log("clean_airport", "missing_tz_name", int(ap["tz_name"].isna().sum()))
    return ap

def clean_airline() -> pd.DataFrame:
    lc = pd.read_csv(os.path.join(RAW_R, "L_UNIQUE_CARRIERS.csv"), encoding="latin-1",
                     low_memory=False)
    al = pd.DataFrame({
        "airline_code": norm_code(lc["Code"]),
        "airline_name": lc["Description"].astype("string").str.strip(),
    }).dropna(subset=["airline_code"]).drop_duplicates(subset=["airline_code"])
    qa_log("clean_airline", "rows_out", len(al))
    return al

def clean_aircraft() -> pd.DataFrame:
    master = pd.read_csv(os.path.join(RAW_R, "faa", "MASTER.txt"), encoding="utf-8-sig",
                         low_memory=False,
                         usecols=["N-NUMBER", "MFR MDL CODE", "YEAR MFR", "NAME",
                                  "STATUS CODE", "TYPE AIRCRAFT"])
    master.columns = ["nnum", "mdl_code", "year_mfr", "registrant", "status_code",
                      "type_aircraft"]
    for c in ["nnum", "mdl_code", "registrant", "status_code"]:
        master[c] = master[c].astype("string").str.strip().str.upper()
    master["year_mfr"] = pd.to_numeric(master["year_mfr"], errors="coerce")
    master = master.dropna(subset=["nnum"]).drop_duplicates(subset=["nnum"])
    qa_log("clean_aircraft", "faa_master_rows", len(master))

    ref = pd.read_csv(os.path.join(RAW_R, "faa", "ACFTREF.txt"), encoding="utf-8-sig",
                      low_memory=False,
                      usecols=["CODE", "MFR", "MODEL", "NO-ENG", "NO-SEATS",
                               "AC-WEIGHT", "SPEED"])
    ref.columns = ["mdl_code", "manufacturer", "model", "n_engines",
                   "certificated_max_seats", "weight_class", "cruise_speed"]
    for c in ["mdl_code", "manufacturer", "model", "weight_class"]:
        ref[c] = ref[c].astype("string").str.strip().str.upper()
    for c in ["n_engines", "certificated_max_seats", "cruise_speed"]:
        ref[c] = pd.to_numeric(ref[c], errors="coerce")
    ref = ref.drop_duplicates(subset=["mdl_code"])
    qa_log("clean_aircraft", "faa_acftref_rows", len(ref))

    ac = master.merge(ref, on="mdl_code", how="left", validate="m:1")
    ac = ac.rename(columns={"nnum": "tail_number"})

    ac.loc[ac["certificated_max_seats"].fillna(0) <= 0, "certificated_max_seats"] = np.nan
    ac.loc[ac["cruise_speed"].fillna(0) <= 0, "cruise_speed"] = np.nan

    out = ac[["tail_number", "manufacturer", "model", "n_engines",
              "certificated_max_seats", "weight_class", "cruise_speed",
              "year_mfr", "registrant", "status_code"]]
    qa_log("clean_aircraft", "rows_out", len(out))
    qa_log("clean_aircraft", "model_resolved_pct",
           f"{100*out['model'].notna().mean():.2f}%")
    return out

def clean_weather() -> pd.DataFrame:
    files = sorted(glob.glob(os.path.join(RAW_W, "*.csv")))
    if not files:
        raise SystemExit("no weather batches found - run 01b_acquire_weather.py first")
    files = [f for f in files if not f.endswith("station_crosswalk.csv")]
    df = pd.concat([pd.read_csv(f, low_memory=False, na_values=["M", "T", ""])
                    for f in files], ignore_index=True)
    qa_log("clean_weather", "rows_in", len(df))

    xw_path = os.path.join(RAW_W, "station_crosswalk.csv")
    df["station"] = norm_code(df["station"])
    if os.path.exists(xw_path):
        xw = pd.read_csv(xw_path)
        mapping = dict(zip(norm_code(xw["station"]), norm_code(xw["airport_code"])))
        df["airport_code"] = df["station"].map(mapping).fillna(df["station"])
        remapped = int((df["airport_code"] != df["station"]).sum())
        qa_log("clean_weather", "observations_remapped_to_iata", remapped)
    else:
        df["airport_code"] = df["station"]

    df["observed_at_utc"] = pd.to_datetime(df["valid"], errors="coerce", utc=True)
    rename = {"tmpf": "temperature_f", "dwpf": "dewpoint_f", "relh": "humidity_pct",
              "vsby": "visibility_mi", "sknt": "wind_speed_kt", "drct": "wind_dir_deg",
              "gust": "wind_gust_kt", "skyc1": "sky_cover"}
    df = df.rename(columns=rename)
    for c in ["temperature_f", "dewpoint_f", "humidity_pct", "visibility_mi",
              "wind_speed_kt", "wind_dir_deg", "wind_gust_kt"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    before = len(df)
    df = df.dropna(subset=["airport_code", "observed_at_utc"])
    qa_log("clean_weather", "dropped_null_key_or_time", before - len(df))

    before = len(df)
    df = df.drop_duplicates(subset=["airport_code", "observed_at_utc"], keep="last")
    qa_log("clean_weather", "dropped_duplicate_observations", before - len(df))

    df["implausible_value"] = False
    for col, (lo, hi) in WEATHER_BOUNDS.items():
        bad = df[col].notna() & ~df[col].between(lo, hi)
        n = int(bad.sum())
        if n:
            qa_log("clean_weather", f"implausible_{col}", n,
                   f"outside [{lo}, {hi}] - value nulled, row flagged; "
                   f"observed max {df.loc[bad, col].abs().max():,.1f}")
        df.loc[bad, col] = pd.NA
        df["implausible_value"] |= bad
    qa_log("clean_weather", "rows_with_any_implausible_value",
           int(df["implausible_value"].sum()))

    out = df[["airport_code", "observed_at_utc", "temperature_f", "dewpoint_f",
              "humidity_pct", "visibility_mi", "wind_speed_kt", "wind_dir_deg",
              "wind_gust_kt", "sky_cover", "implausible_value"]] \
        .sort_values(["airport_code", "observed_at_utc"])
    qa_log("clean_weather", "rows_out", len(out))
    qa_log("clean_weather", "distinct_airports", int(out["airport_code"].nunique()))
    return out.reset_index(drop=True)

def clean_flights(airport: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    files = sorted(glob.glob(os.path.join(RAW_F, "extract_*", "*.csv")))
    if not files:
        raise SystemExit("no extracted flight CSVs found")
    df = pd.concat([pd.read_csv(f, usecols=FLIGHT_COLS, low_memory=False)
                    for f in files], ignore_index=True)
    qa_log("clean_flights", "rows_in", len(df))

    df["origin"] = norm_code(df["Origin"])
    df["dest"] = norm_code(df["Dest"])
    df["airline_code"] = norm_code(df["Reporting_Airline"])
    df["tail_number"] = norm_tail(df["Tail_Number"])
    df["flight_number"] = pd.to_numeric(df["Flight_Number_Reporting_Airline"],
                                        errors="coerce").astype("Int64")
    df["service_date"] = pd.to_datetime(df["FlightDate"], errors="coerce")

    recovered = int((df["Tail_Number"].astype("string").str.strip().str.upper()
                     != ("N" + df["tail_number"].fillna(""))).sum())
    qa_log("clean_flights", "tail_numbers_normalized", recovered,
           "leading-N stripped/added to match FAA registry form")

    before = len(df)
    df = df.drop_duplicates()
    qa_log("clean_flights", "exact_duplicates_removed", before - len(df))

    nat = ["service_date", "airline_code", "flight_number", "origin", "dest"]
    dup_nat = int(df.duplicated(subset=nat).sum())
    qa_log("clean_flights", "duplicate_natural_key", dup_nat,
           "a real airline can legitimately repeat a flight number on a route")

    df["scheduled_dep_local"] = parse_hhmm(df["service_date"], df["CRSDepTime"])
    df["actual_dep_local"] = parse_hhmm(df["service_date"], df["DepTime"])
    df["scheduled_arr_local"] = parse_hhmm(df["service_date"], df["CRSArrTime"])
    df["actual_arr_local"] = parse_hhmm(df["service_date"], df["ArrTime"])

    for sched, act in [("scheduled_arr_local", "scheduled_dep_local"),
                       ("actual_arr_local", "actual_dep_local")]:
        crossed = df[sched].notna() & df[act].notna() & (df[sched] < df[act])
        df.loc[crossed, sched] = df.loc[crossed, sched] + pd.Timedelta(days=1)
        qa_log("clean_flights", f"{sched}_rolled_to_next_day", int(crossed.sum()))

    df["is_cancelled"] = pd.to_numeric(df["Cancelled"], errors="coerce").fillna(0).astype(int) == 1
    df["is_diverted"] = pd.to_numeric(df["Diverted"], errors="coerce").fillna(0).astype(int) == 1
    df["cancellation_code"] = norm_code(df["CancellationCode"])

    df["dep_delay_minutes"] = pd.to_numeric(df["DepDelay"], errors="coerce")
    df["arr_delay_minutes"] = pd.to_numeric(df["ArrDelay"], errors="coerce")

    df["delay_minutes"] = df["arr_delay_minutes"]

    computed = (df["actual_arr_local"] - df["scheduled_arr_local"]).dt.total_seconds() / 60
    both = df["arr_delay_minutes"].notna() & computed.notna()
    mismatch = int((both & ((computed - df["arr_delay_minutes"]).abs() > 1)).sum())
    qa_log("clean_flights", "reported_vs_computed_delay_mismatch", mismatch,
           f"{100*mismatch/max(int(both.sum()),1):.3f}% of comparable rows")

    df["implausible_delay"] = (
        df["delay_minutes"].notna()
        & (~df["delay_minutes"].between(IMPLAUSIBLE_DELAY_MIN, IMPLAUSIBLE_DELAY_MAX))
    )
    df["negative_distance"] = pd.to_numeric(df["Distance"], errors="coerce").fillna(0) <= 0
    qa_log("clean_flights", "implausible_delay_flagged", int(df["implausible_delay"].sum()))
    qa_log("clean_flights", "nonpositive_distance_flagged", int(df["negative_distance"].sum()))

    critical = ["service_date", "airline_code", "origin", "dest", "flight_number"]
    bad_key = df[critical].isna().any(axis=1)
    qa_log("clean_flights", "rows_missing_critical_key", int(bad_key.sum()))
    qa_log("clean_flights", "rows_missing_tail_number", int(df["tail_number"].isna().sum()),
           "optional attribute - kept, aircraft FK will be NULL")

    quarantine = df.loc[bad_key].copy()
    quarantine["quarantine_reason"] = "missing critical business key"
    df = df.loc[~bad_key].copy()

    tz = airport.set_index("airport_code")["tz_name"]
    df["origin_tz"] = df["origin"].map(tz)
    qa_log("clean_flights", "rows_missing_origin_tz", int(df["origin_tz"].isna().sum()))
    df["scheduled_dep_utc"] = _localize_to_utc(df["scheduled_dep_local"], df["origin_tz"])
    qa_log("clean_flights", "scheduled_dep_utc_resolved",
           int(df["scheduled_dep_utc"].notna().sum()))

    thr = CFG["kpi"]["on_time_threshold_minutes"]
    analytical = (~df["is_cancelled"]) & (~df["is_diverted"]) & df["delay_minutes"].notna()
    df["is_analytical"] = analytical
    df["is_on_time"] = pd.Series(pd.NA, index=df.index, dtype="boolean")
    df.loc[analytical, "is_on_time"] = df.loc[analytical, "delay_minutes"] <= thr

    qa_log("clean_flights", "analytical_flights", int(analytical.sum()))
    qa_log("clean_flights", "cancelled_excluded", int(df["is_cancelled"].sum()))
    qa_log("clean_flights", "diverted_excluded", int(df["is_diverted"].sum()))
    qa_log("clean_flights", "on_time_flights", int((df["is_on_time"] == True).sum()))
    qa_log("clean_flights", "on_time_pct",
           f"{100*(df['is_on_time']==True).sum()/max(int(analytical.sum()),1):.2f}%")

    keep = [
        "service_date", "Year", "Month", "DayOfWeek",
        "airline_code", "DOT_ID_Reporting_Airline", "tail_number", "flight_number",
        "origin", "dest", "origin_tz",
        "scheduled_dep_local", "actual_dep_local", "scheduled_arr_local",
        "actual_arr_local", "scheduled_dep_utc",
        "dep_delay_minutes", "arr_delay_minutes", "delay_minutes",
        "is_cancelled", "is_diverted", "cancellation_code",
        "Distance", "CRSElapsedTime", "ActualElapsedTime",
        "CarrierDelay", "WeatherDelay", "NASDelay", "SecurityDelay", "LateAircraftDelay",
        "implausible_delay", "negative_distance", "is_analytical", "is_on_time",
    ]
    out = df[keep].rename(columns={
        "Year": "service_year", "Month": "service_month", "DayOfWeek": "day_of_week",
        "DOT_ID_Reporting_Airline": "dot_airline_id", "Distance": "distance_mi",
        "CRSElapsedTime": "scheduled_elapsed_min", "ActualElapsedTime": "actual_elapsed_min",
        "CarrierDelay": "carrier_delay_min", "WeatherDelay": "weather_delay_min",
        "NASDelay": "nas_delay_min", "SecurityDelay": "security_delay_min",
        "LateAircraftDelay": "late_aircraft_delay_min",
    })
    qa_log("clean_flights", "rows_out", len(out))
    return out.reset_index(drop=True), quarantine

def _localize_to_utc(local: pd.Series, tz: pd.Series) -> pd.Series:
    out = pd.Series(pd.NaT, index=local.index, dtype="datetime64[ns, UTC]")
    for zone, idx in tz.dropna().groupby(tz.dropna()).groups.items():
        vals = local.loc[idx]
        if vals.notna().sum() == 0:
            continue
        try:
            out.loc[idx] = (vals.dt.tz_localize(zone, nonexistent="shift_forward",
                                                ambiguous=True)
                                .dt.tz_convert("UTC"))
        except Exception as exc:
            qa_log("clean_flights", f"tz_localize_failed_{zone}", str(exc)[:60])
    return out

def main() -> None:
    print("=== STAGE 2: CLEAN EACH SOURCE ===\n")

    print("-- airport --")
    airport = clean_airport()
    airport.to_parquet(curated_path("airport_clean.parquet"), index=False)

    print("\n-- airline --")
    clean_airline().to_parquet(curated_path("airline_clean.parquet"), index=False)

    print("\n-- aircraft --")
    clean_aircraft().to_parquet(curated_path("aircraft_clean.parquet"), index=False)

    print("\n-- weather --")
    clean_weather().to_parquet(curated_path("weather_clean.parquet"), index=False)

    print("\n-- flights --")
    flights, quarantine = clean_flights(airport)
    flights.to_parquet(curated_path("flight_clean.parquet"), index=False)
    if len(quarantine):
        quarantine.to_parquet(curated_path("flight_quarantine.parquet"), index=False)

    flush_audit("qa_stage2_clean")
    print("\ncurated outputs written to", CFG["paths"]["curated"])

if __name__ == "__main__":
    main()
