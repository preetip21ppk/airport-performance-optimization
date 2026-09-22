from __future__ import annotations

import os
import sys

import pandas as pd
from sqlalchemy import create_engine, text

from common import CFG, flush_audit, qa_log

M = CFG["mysql"]

def get_engine():
    pw = os.environ.get("MYSQL_PASSWORD")
    if not pw:
        sys.exit("MYSQL_PASSWORD environment variable is not set.")
    return create_engine(
        f"mysql+pymysql://{M['user']}:{pw}@{M['host']}:{M['port']}/{M['database']}"
        "?charset=utf8mb4", future=True, pool_pre_ping=True)

def extract(eng) -> tuple[pd.DataFrame, pd.Series]:
    rule = pd.read_sql(text("""
        SELECT threshold_minutes, target_pct
        FROM KPIThreshold
        WHERE metric_name = 'OnTimePerformance'
        LIMIT 1
    """), eng).iloc[0]
    qa_log("extract", "threshold_minutes", int(rule.threshold_minutes))
    qa_log("extract", "target_pct", float(rule.target_pct))

    flights = pd.read_sql(text("""
        SELECT f.flight_id, f.service_date, f.delay_minutes,
               f.is_cancelled, f.is_diverted, f.is_analytical,
               f.carrier_delay_min, f.weather_delay_min, f.nas_delay_min,
               f.security_delay_min, f.late_aircraft_delay_min,
               al.airline_code, al.airline_name,
               o.airport_code AS origin_code, o.airport_name AS origin_name,
               o.city AS origin_city, o.state AS origin_state,
               w.visibility_mi, w.temperature_f
        FROM Flight f
        JOIN Airline al ON al.airline_id = f.airline_id
        JOIN Airport o  ON o.airport_id  = f.origin_airport_id
        LEFT JOIN Weather w ON w.weather_id = f.weather_id
    """), eng)
    qa_log("extract", "flight_rows_extracted", len(flights))
    return flights, rule

def transform(df: pd.DataFrame, rule: pd.Series) -> dict[str, pd.DataFrame]:
    thr = int(rule.threshold_minutes)
    target = float(rule.target_pct)

    analytical = df["is_analytical"] == 1
    df["is_on_time_calc"] = pd.NA
    df.loc[analytical, "is_on_time_calc"] = (
        df.loc[analytical, "delay_minutes"] <= thr
    )

    total = int(analytical.sum())
    on_time = int((df["is_on_time_calc"] == True).sum())
    delayed = total - on_time
    pct = 100.0 * on_time / total if total else 0.0
    status = "Compliant" if pct >= target else "Non-Compliant"

    qa_log("transform", "analytical_flights", total)
    qa_log("transform", "on_time_flights", on_time)
    qa_log("transform", "on_time_pct", round(pct, 2))
    qa_log("transform", "gap_to_target_pp", round(target - pct, 2))
    qa_log("transform", "compliance_status", status)

    summary = pd.DataFrame([{
        "metric_name": "OnTimePerformance",
        "scheduled_flights": int(len(df)),
        "analytical_flights": total,
        "on_time_flights": on_time,
        "delayed_flights": delayed,
        "cancelled_flights": int((df["is_cancelled"] == 1).sum()),
        "diverted_flights": int((df["is_diverted"] == 1).sum()),
        "on_time_pct": round(pct, 2),
        "target_pct": round(target, 2),
        "gap_to_target_pp": round(target - pct, 2),
        "threshold_minutes": thr,
        "compliance_status": status,
        "avg_delay_minutes": round(float(df.loc[analytical, "delay_minutes"].mean()), 2),
        "median_delay_minutes": float(df.loc[analytical, "delay_minutes"].median()),
        "window_start": df["service_date"].min(),
        "window_end": df["service_date"].max(),
        "calculated_at": pd.Timestamp.utcnow().tz_localize(None),
    }])

    a = df[analytical]

    def agg(group_cols: list[str]) -> pd.DataFrame:
        g = (a.groupby(group_cols, dropna=False)
               .agg(flights=("flight_id", "size"),
                    on_time_flights=("is_on_time_calc", lambda s: int((s == True).sum())),
                    avg_delay_minutes=("delay_minutes", "mean"))
               .reset_index())
        g["on_time_pct"] = (100.0 * g["on_time_flights"] / g["flights"]).round(2)
        g["avg_delay_minutes"] = g["avg_delay_minutes"].round(2)
        g["target_pct"] = round(target, 2)
        g["gap_to_target_pp"] = (g["target_pct"] - g["on_time_pct"]).round(2)
        g["meets_target"] = g["on_time_pct"] >= target
        return g

    by_airline = agg(["airline_code", "airline_name"]).sort_values("on_time_pct")
    by_airport = agg(["origin_code", "origin_name", "origin_city", "origin_state"]) \
        .sort_values("flights", ascending=False)
    by_day = agg(["service_date"]).sort_values("service_date")

    causes = pd.DataFrame([{
        "cause": c.replace("_min", "").replace("_", " ").title(),
        "total_hours": round(float(a[c].fillna(0).sum()) / 60.0, 1),
        "flights_affected": int((a[c].fillna(0) > 0).sum()),
    } for c in ["carrier_delay_min", "late_aircraft_delay_min", "nas_delay_min",
                "weather_delay_min", "security_delay_min"]])
    causes["share_pct"] = (100.0 * causes["total_hours"]
                           / causes["total_hours"].sum()).round(2)
    causes = causes.sort_values("total_hours", ascending=False)

    qa_log("transform", "kpi_by_airline_rows", len(by_airline))
    qa_log("transform", "kpi_by_airport_rows", len(by_airport))
    qa_log("transform", "kpi_by_day_rows", len(by_day))
    qa_log("transform", "airports_meeting_target",
           int(by_airport["meets_target"].sum()),
           f"of {len(by_airport)} airports")

    return {"kpi_summary": summary, "kpi_by_airline": by_airline,
            "kpi_by_airport": by_airport, "kpi_by_day": by_day,
            "kpi_delay_causes": causes}

def load(eng, tables: dict[str, pd.DataFrame]) -> None:
    for name, df in tables.items():
        df.to_sql(name, eng, if_exists="replace", index=False,
                  chunksize=5000, method="multi")
        qa_log("load", f"{name}_rows_written", len(df))
        print(f"  {name:<20} {len(df):>8,} rows")

def validate(eng, tables: dict[str, pd.DataFrame]) -> bool:
    sql = pd.read_sql(text("""
        SELECT COUNT(*)                                                  AS analytical,
               SUM(f.delay_minutes <= k.threshold_minutes)                AS on_time,
               ROUND(100.0 * SUM(f.delay_minutes <= k.threshold_minutes)
                     / COUNT(*), 2)                                       AS on_time_pct
        FROM Flight f
        CROSS JOIN KPIThreshold k
        WHERE k.metric_name = 'OnTimePerformance' AND f.is_analytical = 1
    """), eng).iloc[0]

    py = tables["kpi_summary"].iloc[0]
    checks = [
        ("analytical_flights", int(sql.analytical), int(py.analytical_flights)),
        ("on_time_flights", int(sql.on_time), int(py.on_time_flights)),
        ("on_time_pct", float(sql.on_time_pct), float(py.on_time_pct)),
    ]
    ok = True
    print("\n  reconciliation - independent SQL vs Python ETL")
    for label, s, p in checks:
        match = abs(s - p) < 0.011
        ok &= match
        print(f"    {label:<22} sql={s:<14} python={p:<14} "
              f"{'MATCH' if match else 'MISMATCH'}")
        qa_log("validate", f"reconcile_{label}", "MATCH" if match else "MISMATCH",
               f"sql={s} python={p}")

    total = int(py.analytical_flights)
    parts = int(py.on_time_flights) + int(py.delayed_flights)
    qa_log("validate", "on_time_plus_delayed_equals_total",
           "MATCH" if parts == total else "MISMATCH", f"{parts} vs {total}")
    ok &= parts == total

    by_ap = int(tables["kpi_by_airport"]["flights"].sum())
    qa_log("validate", "airport_subtotals_sum_to_total",
           "MATCH" if by_ap == total else "MISMATCH", f"{by_ap} vs {total}")
    ok &= by_ap == total

    by_day = int(tables["kpi_by_day"]["flights"].sum())
    qa_log("validate", "daily_subtotals_sum_to_total",
           "MATCH" if by_day == total else "MISMATCH", f"{by_day} vs {total}")
    ok &= by_day == total
    return bool(ok)

def main() -> None:
    print("=== STAGE 6: PYTHON ETL - KPI CALCULATION ===\n")
    eng = get_engine()

    print("-- extract --")
    flights, rule = extract(eng)

    print("\n-- transform --")
    tables = transform(flights, rule)
    s = tables["kpi_summary"].iloc[0]
    print(f"\n  {s.analytical_flights:,} analytical flights | "
          f"{s.on_time_flights:,} on time | {s.on_time_pct}% vs target "
          f"{s.target_pct}% -> {s.compliance_status}")

    print("\n-- load --")
    load(eng, tables)

    print("\n-- validate --")
    ok = validate(eng, tables)

    flush_audit("qa_stage6_etl_kpi")
    print("\nETL " + ("RECONCILED" if ok else "FAILED RECONCILIATION"))
    if not ok:
        sys.exit(1)

if __name__ == "__main__":
    main()
