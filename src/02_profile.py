from __future__ import annotations

import glob
import os

import pandas as pd

from common import CFG, QA_DIR

RAW_F = CFG["paths"]["raw_flights"]
RAW_R = CFG["paths"]["raw_reference"]
RAW_W = CFG["paths"]["raw_weather"]

SOURCES = [
    ("flights",          sorted(glob.glob(os.path.join(RAW_F, "extract_*", "*.csv"))), "utf-8"),
    ("airport_lookup",   [os.path.join(RAW_R, "L_AIRPORT.csv")],                        "latin-1"),
    ("airline_lookup",   [os.path.join(RAW_R, "L_UNIQUE_CARRIERS.csv")],                "latin-1"),
    ("ourairports",      [os.path.join(RAW_R, "ourairports_airports.csv")],             "utf-8"),
    ("faa_master",       [os.path.join(RAW_R, "faa", "MASTER.txt")],                    "utf-8-sig"),
    ("faa_acftref",      [os.path.join(RAW_R, "faa", "ACFTREF.txt")],                   "utf-8-sig"),
    ("weather",          sorted(glob.glob(os.path.join(RAW_W, "*.csv"))),               "utf-8"),
]

CANDIDATE_KEYS = {
    "flights": ["FlightDate", "Reporting_Airline", "Flight_Number_Reporting_Airline",
                "Origin", "Dest", "Tail_Number"],
    "airport_lookup": ["Code"],
    "airline_lookup": ["Code"],
    "ourairports": ["iata_code", "ident"],
    "faa_master": ["N-NUMBER", "MFR MDL CODE"],
    "faa_acftref": ["CODE"],
    "weather": ["station", "valid"],
}

def profile(name: str, files: list[str], encoding: str, out) -> None:
    files = [f for f in files if os.path.exists(f)]
    if not files:
        out.write(f"\n{'='*78}\n{name}: NO FILES FOUND\n")
        return

    frames = [pd.read_csv(f, low_memory=False, encoding=encoding) for f in files]
    df = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]

    df = df.loc[:, ~df.columns.str.startswith("Unnamed")]

    out.write(f"\n{'='*78}\n")
    out.write(f"SOURCE: {name}\n")
    out.write(f"files       : {len(files)}\n")
    for f in files[:3]:
        out.write(f"              {os.path.basename(f)}\n")
    if len(files) > 3:
        out.write(f"              ... and {len(files)-3} more\n")
    out.write(f"encoding    : {encoding}\n")
    out.write(f"shape       : {df.shape[0]:,} rows x {df.shape[1]} columns\n")
    out.write(f"exact dupes : {df.duplicated().sum():,}\n")

    out.write("\n  column                          dtype        nulls      null%   nunique\n")
    out.write("  " + "-" * 72 + "\n")
    for c in df.columns:
        n = df[c].isna().sum()
        out.write(f"  {c[:30]:<30}  {str(df[c].dtype):<11}  {n:>9,}  {100*n/len(df):>7.2f}  "
                  f"{df[c].nunique(dropna=True):>9,}\n")

    keys = [k for k in CANDIDATE_KEYS.get(name, []) if k in df.columns]
    if keys:
        out.write("\n  candidate key analysis\n")
        for k in keys:
            nulls = int(df[k].isna().sum())
            dups = int(df[k].duplicated().sum())
            verdict = "UNIQUE" if dups == 0 and nulls == 0 else "not a key on its own"
            out.write(f"    {k:<38} nulls={nulls:>8,}  dup_values={dups:>8,}  -> {verdict}\n")
        if len(keys) > 1:
            combo_dups = int(df[keys].duplicated().sum())
            out.write(f"    COMPOSITE {keys} -> duplicate combinations = {combo_dups:,}\n")

    num = df.select_dtypes("number")
    if not num.empty:
        out.write("\n  numeric ranges (look for negatives / impossible extremes)\n")
        desc = num.describe().T[["min", "50%", "max"]]
        for c, r in desc.iterrows():
            out.write(f"    {str(c)[:32]:<32} min={r['min']:>14,.2f}  median={r['50%']:>12,.2f}  "
                      f"max={r['max']:>14,.2f}\n")

    obj = df.select_dtypes("object")
    flagged = []
    for c in obj.columns:
        s = df[c].dropna().astype(str)
        if s.empty:
            continue
        if (s != s.str.strip()).any():
            flagged.append(f"{c} (leading/trailing whitespace)")
        elif s.nunique() != s.str.strip().str.upper().nunique():
            flagged.append(f"{c} (case variants)")
    if flagged:
        out.write("\n  text inconsistency found in:\n")
        for f in flagged[:15]:
            out.write(f"    - {f}\n")

def main() -> None:
    os.makedirs(QA_DIR, exist_ok=True)
    path = os.path.join(QA_DIR, "profile_report.txt")
    with open(path, "w", encoding="utf-8") as out:
        out.write("AIRPORT PERFORMANCE OPTIMIZATION - RAW SOURCE PROFILING REPORT\n")
        out.write("Stage 1: profile each source independently, before any merge.\n")
        for name, files, enc in SOURCES:
            print(f"profiling {name} ...")
            profile(name, files, enc, out)
    print(f"\nprofile report -> {path}")

if __name__ == "__main__":
    main()
