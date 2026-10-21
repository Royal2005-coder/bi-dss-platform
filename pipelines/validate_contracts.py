#!/usr/bin/env python3
import datetime as dt
import json
import os
import sys

import duckdb
import yaml

DB = os.environ.get("BI_DUCKDB_PATH", "/data/warehouse/warehouse.duckdb")
CONTRACTS = os.environ.get("BI_CONTRACTS", "/workspace/project/contracts/data_contracts.yml")
REPORT = os.environ.get("BI_CONTRACT_REPORT", "/data/exports/powerbi/contract_report.json")


def main():
    with open(CONTRACTS, encoding="utf8") as fh:
        doc = yaml.safe_load(fh)
    con = duckdb.connect(DB)
    con.execute("CREATE SCHEMA IF NOT EXISTS meta")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS meta.contract_validation (
            run_at TIMESTAMP,
            contract_id VARCHAR,
            table_name VARCHAR,
            rule VARCHAR,
            status VARCHAR,
            observed VARCHAR,
            expected VARCHAR
        )
        """
    )
    run_at = dt.datetime.now()
    results = []
    failures = 0

    def add(contract_id, table, rule, status, observed, expected):
        nonlocal failures
        if status == "FAIL":
            failures += 1
        results.append({
            "contract_id": contract_id,
            "table": table,
            "rule": rule,
            "status": status,
            "observed": str(observed),
            "expected": str(expected),
        })

    for c in doc["contracts"]:
        cid = c["id"]
        table = c["table"]
        schema, name = table.split(".", 1)
        exists = con.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
            [schema, name],
        ).fetchone()[0]
        if not exists:
            add(cid, table, "table_exists", "FAIL", 0, 1)
            continue
        add(cid, table, "table_exists", "PASS", 1, 1)

        actual_cols = {
            r[0]: r[1]
            for r in con.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = ? AND table_name = ?",
                [schema, name],
            ).fetchall()
        }
        for col, expected_type in c.get("columns", {}).items():
            actual_type = actual_cols.get(col)
            status = "PASS" if actual_type == expected_type else "FAIL"
            add(cid, table, f"type:{col}", status, actual_type, expected_type)
        extra = set(actual_cols) - set(c.get("columns", {}))
        add(cid, table, "no_extra_columns", "PASS" if not extra else "FAIL", ",".join(sorted(extra)), "")

        if "expected_rows" in c:
            n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            add(cid, table, "row_count", "PASS" if n == c["expected_rows"] else "FAIL", n, c["expected_rows"])

        pk = c.get("primary_key")
        if pk:
            nulls, dup = con.execute(
                f"SELECT COUNT(*) FILTER (WHERE {pk} IS NULL), "
                f"COUNT(*) - COUNT(DISTINCT {pk}) FROM {table}"
            ).fetchone()
            add(cid, table, f"pk_not_null:{pk}", "PASS" if nulls == 0 else "FAIL", nulls, 0)
            add(cid, table, f"pk_unique:{pk}", "PASS" if dup == 0 else "FAIL", dup, 0)

        for rule in c.get("rules", []):
            typ = rule["type"]
            col = rule.get("column")
            if typ == "not_null":
                n = con.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} IS NULL").fetchone()[0]
                add(cid, table, f"not_null:{col}", "PASS" if n == 0 else "FAIL", n, 0)
            elif typ == "unique":
                n = con.execute(f"SELECT COUNT(*) - COUNT(DISTINCT {col}) FROM {table}").fetchone()[0]
                add(cid, table, f"unique:{col}", "PASS" if n == 0 else "FAIL", n, 0)
            elif typ == "accepted_values":
                vals = ", ".join("NULL" if v is None else repr(v) for v in rule["values"])
                n = con.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {col} IS NOT NULL AND {col} NOT IN ({vals})"
                ).fetchone()[0]
                add(cid, table, f"accepted_values:{col}", "PASS" if n == 0 else "FAIL", n, vals)
            elif typ == "positive":
                n = con.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} <= 0 OR {col} IS NULL").fetchone()[0]
                add(cid, table, f"positive:{col}", "PASS" if n == 0 else "FAIL", n, "> 0")
            elif typ == "foreign_key":
                ref_table = rule["ref_table"]
                ref_col = rule["ref_column"]
                n = con.execute(
                    f"SELECT COUNT(*) FROM {table} t WHERE t.{col} IS NOT NULL "
                    f"AND NOT EXISTS (SELECT 1 FROM {ref_table} r WHERE r.{ref_col} = t.{col})"
                ).fetchone()[0]
                add(cid, table, f"foreign_key:{col}", "PASS" if n == 0 else "FAIL", n, 0)
            else:
                add(cid, table, f"unknown_rule:{typ}", "FAIL", typ, "known rule")

    con.executemany(
        "INSERT INTO meta.contract_validation VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (run_at, r["contract_id"], r["table"], r["rule"], r["status"], r["observed"], r["expected"])
            for r in results
        ],
    )
    con.close()

    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    report = {
        "run_at": run_at.isoformat(),
        "contracts_file": CONTRACTS,
        "database": DB,
        "checks": len(results),
        "failures": failures,
        "results": results,
    }
    with open(REPORT, "w", encoding="utf8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(f"contract checks: {len(results)}, failures: {failures}")
    print("report:", REPORT)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
