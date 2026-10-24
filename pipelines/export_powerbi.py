#!/usr/bin/env python3
import csv
import datetime as dt
import hashlib
import json
import os

import duckdb

DB = os.environ.get("BI_DUCKDB_PATH", "/data/warehouse/warehouse.duckdb")
OUT = os.environ.get("BI_EXPORT_DIR", "/data/exports/powerbi")
TABLES = [
    ("DimDate", "dim_date", "date_key"),
    ("DimCarrier", "dim_carrier", "carrier_key"),
    ("DimAirport", "dim_airport", "airport_key"),
    ("DimRoute", "dim_route", "route_key"),
    ("DimDelayReason", "dim_delay_reason", "delay_reason_key"),
    ("DimFlightStatus", "dim_flight_status", "flight_status_key"),
    ("FactFlight", "fact_flight", "flight_key"),
    ("FactDelay", "fact_delay_reason", "delay_fact_key"),
]
# Only conformed dimensions filter facts. Airport destination is an inactive role.
RELATIONSHIPS = [
    ("FactFlight", "DateKey", "DimDate", "DateKey", "many_to_one", True, "Single", ""),
    ("FactFlight", "CarrierKey", "DimCarrier", "CarrierKey", "many_to_one", True, "Single", ""),
    ("FactFlight", "OriginAirportKey", "DimAirport", "AirportKey", "many_to_one", True, "Single", ""),
    ("FactFlight", "DestAirportKey", "DimAirport", "AirportKey", "many_to_one", False, "Single", "Destination role: use USERELATIONSHIP and disable origin role"),
    ("FactFlight", "RouteKey", "DimRoute", "RouteKey", "many_to_one", True, "Single", ""),
    ("FactFlight", "FlightStatusKey", "DimFlightStatus", "FlightStatusKey", "many_to_one", True, "Single", ""),
    ("FactDelay", "DateKey", "DimDate", "DateKey", "many_to_one", True, "Single", ""),
    ("FactDelay", "CarrierKey", "DimCarrier", "CarrierKey", "many_to_one", True, "Single", ""),
    ("FactDelay", "OriginAirportKey", "DimAirport", "AirportKey", "many_to_one", True, "Single", ""),
    ("FactDelay", "DelayReasonKey", "DimDelayReason", "DelayReasonKey", "many_to_one", True, "Single", ""),
]

COLUMN_ALIASES = {
    "state_code": "State",
    "flight_status_code": "StatusCode",
    "flight_status_name": "StatusName",
    "delay_reason_code": "Code",
    "delay_reason_name": "ReasonName",
    "excess_delay_minutes": "Excess_Delay_Minutes",
}


def export_column(name):
    return COLUMN_ALIASES.get(name, "".join(token.capitalize() for token in name.split("_")))


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    os.makedirs(OUT, exist_ok=True)
    con = duckdb.connect(DB, read_only=True)
    manifest = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "database": DB,
        "export_dir": OUT,
        "tables": [],
        "relationships": [
            {
                "from_table": r[0],
                "from_column": r[1],
                "to_table": r[2],
                "to_column": r[3],
                "cardinality": r[4],
                "active": r[5],
                "cross_filter_direction": r[6],
                "note": r[7],
            }
            for r in RELATIONSHIPS
        ],
    }
    for name, table, order_by in TABLES:
        path = os.path.join(OUT, f"{name}.csv")
        cur = con.execute(f"SELECT * FROM marts.{table} ORDER BY {order_by}")
        columns = [export_column(d[0]) for d in cur.description]
        n = 0
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh, lineterminator="\n")
            writer.writerow(columns)
            while True:
                rows = cur.fetchmany(50000)
                if not rows:
                    break
                writer.writerows(rows)
                n += len(rows)
        manifest["tables"].append({
            "table": name,
            "source": f"marts.{table}",
            "file": os.path.basename(path),
            "rows": n,
            "columns": columns,
            "bytes": os.path.getsize(path),
            "sha256": sha256(path),
        })
        print(f"{name}: {n:,} rows, {os.path.getsize(path):,} bytes")
    rel_path = os.path.join(OUT, "powerbi_relationships.csv")
    with open(rel_path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["FromTable", "FromColumn", "ToTable", "ToColumn", "Cardinality", "Active", "CrossFilterDirection", "Note"])
        writer.writerows(RELATIONSHIPS)
    manifest["relationships_file"] = os.path.basename(rel_path)
    with open(os.path.join(OUT, "_manifest.json"), "w", encoding="utf8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    print("manifest:", os.path.join(OUT, "_manifest.json"))


if __name__ == "__main__":
    main()
