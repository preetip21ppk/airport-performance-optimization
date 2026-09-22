from __future__ import annotations

import os
import sys

import pandas as pd
from pymongo import MongoClient
from sqlalchemy import create_engine, text

from common import CFG, flush_audit, qa_log

M = CFG["mysql"]
MG = CFG["mongodb"]
BATCH = 20000

def mysql_engine():
    pw = os.environ.get("MYSQL_PASSWORD")
    if not pw:
        sys.exit("MYSQL_PASSWORD environment variable is not set.")
    return create_engine(
        f"mysql+pymysql://{M['user']}:{pw}@{M['host']}:{M['port']}/{M['database']}"
        "?charset=utf8mb4", future=True, pool_pre_ping=True)

def build_documents(eng, db) -> int:
    coll = db["flight"]
    coll.drop()

    threshold = pd.read_sql(text(
        "SELECT threshold_minutes FROM KPIThreshold "
        "WHERE metric_name = 'OnTimePerformance' LIMIT 1"), eng).iloc[0, 0]
    qa_log("mongo", "threshold_used", int(threshold))

    query = text("""
        SELECT f.flight_id, f.service_date, f.flight_number,
               f.delay_minutes,
               f.is_cancelled, f.is_diverted, f.is_analytical, f.is_on_time,
               al.airline_code, al.airline_name,
               o.airport_code AS origin_code, o.airport_name AS origin_name,
               o.city AS origin_city, o.state AS origin_state,
               d.airport_code AS dest_code, d.airport_name AS dest_name,
               ac.tail_number, ac.manufacturer, ac.model,
               w.temperature_f, w.visibility_mi
        FROM Flight f
        JOIN Airline al ON al.airline_id = f.airline_id
        JOIN Airport o  ON o.airport_id  = f.origin_airport_id
        JOIN Airport d  ON d.airport_id  = f.dest_airport_id
        LEFT JOIN Aircraft ac ON ac.aircraft_id = f.aircraft_id
        LEFT JOIN Weather  w  ON w.weather_id   = f.weather_id
    """)

    inserted = 0
    for chunk in pd.read_sql(query, eng, chunksize=BATCH):
        docs = []
        for r in chunk.itertuples(index=False):
            docs.append({
                "_id": int(r.flight_id),
                "service_date": pd.Timestamp(r.service_date).to_pydatetime(),
                "flight_number": None if pd.isna(r.flight_number) else int(r.flight_number),

                "airline": {"code": r.airline_code, "name": r.airline_name},
                "origin": {"code": r.origin_code, "name": r.origin_name,
                           "city": r.origin_city, "state": r.origin_state},
                "destination": {"code": r.dest_code, "name": r.dest_name},
                "aircraft": None if pd.isna(r.tail_number) else {
                    "tail_number": r.tail_number,
                    "manufacturer": r.manufacturer,
                    "model": r.model,
                },
                "weather": None if pd.isna(r.temperature_f) else {
                    "temperature_f": float(r.temperature_f),
                    "visibility_mi": None if pd.isna(r.visibility_mi) else float(r.visibility_mi),
                },
                "performance": {
                    "delay_minutes": None if pd.isna(r.delay_minutes) else int(r.delay_minutes),
                    "is_cancelled": bool(r.is_cancelled),
                    "is_diverted": bool(r.is_diverted),
                    "is_analytical": bool(r.is_analytical),
                    "is_on_time": None if pd.isna(r.is_on_time) else bool(r.is_on_time),
                },
            })
        coll.insert_many(docs, ordered=False)
        inserted += len(docs)
        print(f"    inserted {inserted:,} documents", end="\r")

    print(f"    inserted {inserted:,} documents")
    coll.create_index("performance.is_analytical")
    coll.create_index("airline.code")
    coll.create_index("origin.code")
    coll.create_index("service_date")
    qa_log("mongo", "documents_inserted", inserted)
    return inserted

def reconcile(eng, db) -> bool:
    coll = db["flight"]
    ok = True

    def check(label: str, sql_val, mongo_val, tol: float = 0.011) -> None:
        nonlocal ok
        if isinstance(sql_val, float) or isinstance(mongo_val, float):
            match = abs(float(sql_val) - float(mongo_val)) < tol
        else:
            match = sql_val == mongo_val
        ok &= match
        print(f"    {label:<34} mysql={str(sql_val):<14} mongo={str(mongo_val):<14} "
              f"{'MATCH' if match else 'MISMATCH'}")
        qa_log("reconcile", label, "MATCH" if match else "MISMATCH",
               f"mysql={sql_val} mongo={mongo_val}")

    with eng.connect() as con:

        check("total_flight_records",
              con.execute(text("SELECT COUNT(*) FROM Flight")).scalar_one(),
              coll.count_documents({}))

        check("analytical_flights",
              con.execute(text(
                  "SELECT COUNT(*) FROM Flight WHERE is_analytical = 1")).scalar_one(),
              coll.count_documents({"performance.is_analytical": True}))

        sql_kpi = con.execute(text("""
            SELECT ROUND(100.0 * SUM(is_on_time = 1) / COUNT(*), 2)
            FROM Flight WHERE is_analytical = 1
        """)).scalar_one()
        agg = list(coll.aggregate([
            {"$match": {"performance.is_analytical": True}},
            {"$group": {"_id": None,
                        "total": {"$sum": 1},
                        "on_time": {"$sum": {"$cond": ["$performance.is_on_time", 1, 0]}}}},
            {"$project": {"pct": {"$round": [{"$multiply":
                          [100, {"$divide": ["$on_time", "$total"]}]}, 2]}}},
        ]))
        check("on_time_pct", float(sql_kpi), float(agg[0]["pct"]))

        check("max_delay_minutes",
              con.execute(text(
                  "SELECT MAX(delay_minutes) FROM Flight WHERE is_analytical = 1")).scalar_one(),
              list(coll.aggregate([
                  {"$match": {"performance.is_analytical": True}},
                  {"$group": {"_id": None, "m": {"$max": "$performance.delay_minutes"}}}]))[0]["m"])
        check("min_delay_minutes",
              con.execute(text(
                  "SELECT MIN(delay_minutes) FROM Flight WHERE is_analytical = 1")).scalar_one(),
              list(coll.aggregate([
                  {"$match": {"performance.is_analytical": True}},
                  {"$group": {"_id": None, "m": {"$min": "$performance.delay_minutes"}}}]))[0]["m"])

        sql_car = pd.read_sql(text("""
            SELECT al.airline_code AS code, COUNT(*) AS flights
            FROM Flight f JOIN Airline al ON al.airline_id = f.airline_id
            WHERE f.is_analytical = 1
            GROUP BY al.airline_code ORDER BY al.airline_code
        """), con)
        mg_car = pd.DataFrame(list(coll.aggregate([
            {"$match": {"performance.is_analytical": True}},
            {"$group": {"_id": "$airline.code", "flights": {"$sum": 1}}},
            {"$sort": {"_id": 1}},
        ]))).rename(columns={"_id": "code"})
        merged = sql_car.merge(mg_car, on="code", how="outer",
                               suffixes=("_sql", "_mongo"))
        bad = merged[merged["flights_sql"] != merged["flights_mongo"]]
        check("carrier_distribution_rows", len(sql_car), len(mg_car))
        check("carrier_counts_disagreeing", 0, len(bad))

        sql_ap = con.execute(text("""
            SELECT COUNT(DISTINCT origin_airport_id) FROM Flight WHERE is_analytical = 1
        """)).scalar_one()
        mg_ap = len(coll.distinct("origin.code", {"performance.is_analytical": True}))
        check("distinct_origin_airports", sql_ap, mg_ap)

        sql_dates = con.execute(text(
            "SELECT MIN(service_date), MAX(service_date) FROM Flight")).one()
        mg_min = coll.find_one(sort=[("service_date", 1)])["service_date"].date()
        mg_max = coll.find_one(sort=[("service_date", -1)])["service_date"].date()
        check("window_start", str(sql_dates[0]), str(mg_min))
        check("window_end", str(sql_dates[1]), str(mg_max))

    return bool(ok)

def main() -> None:
    print("=== STAGE 7: MONGODB DOCUMENT MODEL + RECONCILIATION ===\n")
    eng = mysql_engine()
    client = MongoClient(MG["uri"], serverSelectionTimeoutMS=8000)
    client.admin.command("ping")
    db = client[MG["database"]]
    print(f"connected to MongoDB at {MG['uri']}, database '{MG['database']}'")

    print("\n-- building document collection from MySQL --")
    build_documents(eng, db)

    print("\n-- reconciling MySQL <-> MongoDB --")
    ok = reconcile(eng, db)

    flush_audit("qa_stage7_mongo_reconcile")
    print("\nRECONCILIATION " + ("PASSED" if ok else "FAILED"))
    client.close()
    if not ok:
        sys.exit(1)

if __name__ == "__main__":
    main()
