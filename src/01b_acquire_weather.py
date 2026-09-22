import glob
import io
import os
import sys
import time

import pandas as pd
import requests
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "..", "config", "config.yaml"), encoding="utf-8") as fh:
    CFG = yaml.safe_load(fh)

RAW_FLIGHTS = CFG["paths"]["raw_flights"]
RAW_WEATHER = CFG["paths"]["raw_weather"]
IEM_URL = CFG["sources"]["weather"]["url"]

VARIABLES = ["tmpf", "dwpf", "relh", "vsby", "sknt", "drct", "gust", "skyc1"]

BATCH_SIZE = 20
MAX_RETRIES = 4
RETRY_SLEEP = 5

def discover_stations() -> tuple[list[str], dict[str, str]]:
    files = sorted(glob.glob(os.path.join(RAW_FLIGHTS, "extract_*", "*.csv")))
    if not files:
        sys.exit(f"No extracted flight CSVs found under {RAW_FLIGHTS}. Run 01a first.")

    codes: set[str] = set()
    for f in files:
        df = pd.read_csv(f, usecols=["Origin", "Dest"], low_memory=False)
        codes.update(df["Origin"].dropna().astype(str).str.strip().str.upper())
        codes.update(df["Dest"].dropna().astype(str).str.strip().str.upper())

    xwalk = {k.upper(): v.upper() for k, v in (CFG.get("weather_station_crosswalk") or {}).items()}
    station_to_airport = {xwalk.get(c, c): c for c in sorted(codes)}

    os.makedirs(RAW_WEATHER, exist_ok=True)
    pd.DataFrame({"station": list(station_to_airport),
                  "airport_code": list(station_to_airport.values())}) \
      .to_csv(os.path.join(RAW_WEATHER, "station_crosswalk.csv"), index=False)

    return sorted(station_to_airport), station_to_airport

def fetch_batch(stations: list[str]) -> pd.DataFrame | None:
    months = CFG["window"]["months"]
    start = min((m["year"], m["month"]) for m in months)
    end = max((m["year"], m["month"]) for m in months)

    end_year, end_month = (end[0] + 1, 1) if end[1] == 12 else (end[0], end[1] + 1)

    params = [
        ("data", v) for v in VARIABLES
    ] + [
        ("station", s) for s in stations
    ] + [
        ("year1", start[0]), ("month1", start[1]), ("day1", 1),
        ("year2", end_year), ("month2", end_month), ("day2", 1),
        ("tz", "UTC"),
        ("format", "onlycomma"),
        ("latlon", "yes"),
        ("elev", "no"),
        ("missing", "M"),
        ("trace", "T"),
        ("direct", "no"),
        ("report_type", 3),
        ("report_type", 4),
    ]

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(IEM_URL, params=params, timeout=600)
            r.raise_for_status()
            text = r.text
            if not text.strip() or text.lstrip().startswith("<"):
                raise ValueError("empty or non-CSV response")
            return pd.read_csv(io.StringIO(text), low_memory=False)
        except Exception as exc:
            if attempt == MAX_RETRIES:
                print(f"    give up after {MAX_RETRIES} attempts: {exc}")
                return None
            print(f"    attempt {attempt} failed ({exc}); retrying in {RETRY_SLEEP}s")
            time.sleep(RETRY_SLEEP)
    return None

def main() -> None:
    os.makedirs(RAW_WEATHER, exist_ok=True)
    stations, xwalk = discover_stations()
    remapped = sum(1 for s, a in xwalk.items() if s != a)
    print(f"airports found in real flight data: {len(stations)} "
          f"({remapped} requested under a different station id)")

    batches = [stations[i:i + BATCH_SIZE] for i in range(0, len(stations), BATCH_SIZE)]
    total_rows = 0

    for i, batch in enumerate(batches, start=1):
        out = os.path.join(RAW_WEATHER, f"iem_metar_batch_{i:03d}.csv")
        if os.path.exists(out) and os.path.getsize(out) > 0:
            print(f"[{i}/{len(batches)}] cached, skipping")
            total_rows += sum(1 for _ in open(out, encoding="utf-8")) - 1
            continue

        print(f"[{i}/{len(batches)}] {batch[0]}..{batch[-1]} ({len(batch)} stations)")
        df = fetch_batch(batch)
        if df is None or df.empty:
            print("    no data returned for this batch")
            continue

        df.to_csv(out, index=False)
        total_rows += len(df)
        print(f"    {len(df):,} observations -> {os.path.basename(out)}")

    print(f"\ndone. {total_rows:,} weather observations in {RAW_WEATHER}")

if __name__ == "__main__":
    main()
