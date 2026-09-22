from __future__ import annotations

import os
import sys
import time

import pandas as pd
from sqlalchemy import create_engine, text

from common import CFG, flush_audit, qa_log

CURATED = CFG["paths"]["curated"]
SQL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sql")
M = CFG["mysql"]

LOAD_ORDER = ["KPIThreshold", "Airport", "Airline", "Aircraft", "Weather",
              "Flight", "Passenger", "BoardingPass", "Baggage"]

CHUNK = 5000

def engine_for(database: str | None):
    pw = os.environ.get("MYSQL_PASSWORD")
    if not pw:
        sys.exit("MYSQL_PASSWORD environment variable is not set.")
    db = f"/{database}" if database else "/"
    url = (f"mysql+pymysql://{M['user']}:{pw}@{M['host']}:{M['port']}{db}"
           "?charset=utf8mb4")
    return create_engine(url, future=True, pool_pre_ping=True)

def split_statements(script: str) -> list[str]:
    out, buf = [], []
    quote = None
    i, n = 0, len(script)
    while i < n:
        ch = script[i]
        nxt = script[i + 1] if i + 1 < n else ""

        if quote:
            buf.append(ch)
            if ch == "\\" and nxt:
                buf.append(nxt)
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue

        if ch in ("'", '"', "`"):
            quote = ch
            buf.append(ch)
            i += 1
            continue

        if ch == "-" and nxt == "-":
            while i < n and script[i] != "\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":
            i += 2
            while i < n - 1 and not (script[i] == "*" and script[i + 1] == "/"):
                i += 1
            i += 2
            continue

        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out

def run_script(eng, path: str) -> None:
    with open(path, encoding="utf-8") as fh:
        stmts = split_statements(fh.read())
    with eng.begin() as con:
        for s in stmts:
            con.execute(text(s))
    print(f"  executed {len(stmts)} statements from {os.path.basename(path)}")

def mysql_ready(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        s = out[c]
        if isinstance(s.dtype, pd.DatetimeTZDtype):
            out[c] = s.dt.tz_convert("UTC").dt.tz_localize(None)
        elif str(s.dtype) == "boolean":
            out[c] = s.astype("object")
        elif str(s.dtype) in ("Int64", "Int32", "Float64"):
            out[c] = s.astype("object")
    return out.astype(object).where(pd.notna(out), None)

def main() -> None:
    print("=== STAGE 5: LOAD INTO MYSQL ===\n")

    print("-- creating schema --")
    run_script(engine_for(None), os.path.join(SQL_DIR, "01_schema.sql"))

    eng = engine_for(M["database"])
    total_rows = 0
    t_all = time.time()

    for name in LOAD_ORDER:
        path = os.path.join(CURATED, f"entity_{name}.parquet")
        if not os.path.exists(path):
            sys.exit(f"missing {path} - run stages 4 and 4b first")
        df = pd.read_parquet(path)
        df = mysql_ready(df)

        t0 = time.time()
        df.to_sql(name, eng, if_exists="append", index=False,
                  chunksize=CHUNK, method="multi")
        dt = time.time() - t0
        total_rows += len(df)
        print(f"  {name:<14} {len(df):>10,} rows in {dt:6.1f}s "
              f"({len(df)/max(dt, 0.01):>8,.0f} rows/s)")
        qa_log("load", f"{name}_rows_loaded", len(df))

    print(f"\nloaded {total_rows:,} rows in {time.time()-t_all:.1f}s")

    print("\n-- creating indexes --")
    run_script(eng, os.path.join(SQL_DIR, "02_indexes.sql"))

    print("\n-- creating Power BI views --")
    run_script(eng, os.path.join(SQL_DIR, "04_powerbi_views.sql"))

    print("\n-- verifying row counts in MySQL against the curated files --")
    ok = True
    with eng.connect() as con:
        for name in LOAD_ORDER:
            expected = len(pd.read_parquet(os.path.join(CURATED, f"entity_{name}.parquet")))
            actual = con.execute(text(f"SELECT COUNT(*) FROM {name}")).scalar_one()
            match = "OK" if actual == expected else "MISMATCH"
            if actual != expected:
                ok = False
            print(f"  {name:<14} curated {expected:>10,} | mysql {actual:>10,}  {match}")
            qa_log("verify", f"{name}_rowcount_match", match,
                   f"curated={expected:,} mysql={actual:,}")

        row = con.execute(text("""
            SELECT COUNT(*)                                       AS analytical,
                   SUM(is_on_time = 1)                            AS on_time,
                   ROUND(100 * SUM(is_on_time = 1) / COUNT(*), 2)  AS on_time_pct
            FROM Flight
            WHERE is_analytical = 1
        """)).mappings().one()
        print(f"\n  SQL-side KPI: {row['analytical']:,} analytical flights, "
              f"{row['on_time']:,} on time = {row['on_time_pct']}%")
        qa_log("verify", "sql_on_time_pct", str(row["on_time_pct"]))

        orphans = con.execute(text("""
            SELECT
              (SELECT COUNT(*) FROM Flight f LEFT JOIN Airport a
                 ON f.origin_airport_id = a.airport_id WHERE a.airport_id IS NULL) AS bad_origin,
              (SELECT COUNT(*) FROM Flight f LEFT JOIN Airline al
                 ON f.airline_id = al.airline_id WHERE al.airline_id IS NULL)      AS bad_airline,
              (SELECT COUNT(*) FROM BoardingPass b LEFT JOIN Flight f
                 ON b.flight_id = f.flight_id WHERE f.flight_id IS NULL)           AS bad_bp_flight
        """)).mappings().one()
        print(f"  orphan check: {dict(orphans)}")
        for k, v in orphans.items():
            qa_log("verify", f"orphans_{k}", int(v), "must be 0")
            if v:
                ok = False

    flush_audit("qa_stage5_load")
    print("\nLOAD " + ("VERIFIED" if ok else "FAILED VERIFICATION"))
    if not ok:
        sys.exit(1)

if __name__ == "__main__":
    main()
