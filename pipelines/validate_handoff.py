"""Validate the files Phuc actually imports, not only warehouse tables."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

import duckdb

from export_powerbi import TABLES, RELATIONSHIPS, export_column


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument('--directory', type=Path, default=root / 'SkyOps_Technical/data/processed')
    parser.add_argument('--database', type=Path, help='Optional dbt warehouse for full value comparison')
    parser.add_argument('--report', type=Path, default=root / 'SkyOps_Technical/outputs/validation_report.json')
    args = parser.parse_args()
    con = duckdb.connect()
    if args.database:
        database_literal = str(args.database).replace("'", "''")
        con.execute(f"ATTACH '{database_literal}' AS warehouse (READ_ONLY)")
    checks, tables = [], []

    def check(name, observed, expected=0):
        checks.append(dict(check=name, observed=observed, expected=expected, passed=observed == expected))

    def scalar(sql):
        return con.execute(sql).fetchone()[0]

    for name, table, key in TABLES:
        path = args.directory / f'{name}.csv'
        with path.open(encoding='utf-8-sig', newline='') as stream:
            columns = next(csv.reader(stream))
        con.execute(f'CREATE TABLE "{name}" AS SELECT * FROM read_csv(?, header=true, sample_size=-1)', [str(path)])
        pk = export_column(key)
        count = scalar(f'SELECT count(*) FROM "{name}"')
        check(f'{name}: primary key NULL/duplicate', scalar(f'SELECT count(*)-count(DISTINCT "{pk}") FROM "{name}"'))
        h = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                h.update(chunk)
        tables.append(dict(table=name, file=path.name, rows=count, columns=columns, bytes=path.stat().st_size, sha256=h.hexdigest()))
        if args.database:
            schema = con.execute(f'DESCRIBE warehouse.marts.{table}').fetchall()
            check(f'{name}: schema', columns, [export_column(s[0]) for s in schema])
            expressions = ', '.join(f'CAST("{export_column(s[0])}" AS {s[1]}) AS "{s[0]}"' for s in schema)
            # Route mean may differ in floating-point summation between Pandas and SQL.
            if name == 'DimRoute':
                expressions = expressions.replace('CAST("DistanceMiles" AS DOUBLE)', 'round(CAST("DistanceMiles" AS DOUBLE), 8)')
                expected = ', '.join('round(distance_miles, 8) AS distance_miles' if s[0] == 'distance_miles' else s[0] for s in schema)
            else:
                expected = '*'
            con.execute(f'CREATE TEMP VIEW actual_rows AS SELECT {expressions} FROM "{name}"')
            con.execute(f'CREATE TEMP VIEW expected_rows AS SELECT {expected} FROM warehouse.marts.{table}')
            check(f'{name}: full values vs dbt (symmetric difference)', scalar('SELECT (SELECT count(*) FROM (SELECT * FROM actual_rows EXCEPT ALL SELECT * FROM expected_rows)) + (SELECT count(*) FROM (SELECT * FROM expected_rows EXCEPT ALL SELECT * FROM actual_rows))'))
            con.execute('DROP VIEW actual_rows')
            con.execute('DROP VIEW expected_rows')

    for fact, fk, dim, pk, *_ in RELATIONSHIPS:
        check(f'{fact}.{fk}: FK {dim}.{pk}', scalar(f'SELECT count(*) FROM "{fact}" f LEFT JOIN "{dim}" d ON f."{fk}"=d."{pk}" WHERE d."{pk}" IS NULL'))
    check('FactDelay: FlightKey orphan', scalar('SELECT count(*) FROM FactDelay d LEFT JOIN FactFlight f USING (FlightKey) WHERE f.FlightKey IS NULL'))
    check('FactDelay: shared keys agree with flight', scalar('SELECT count(*) FROM FactDelay d JOIN FactFlight f USING (FlightKey) WHERE d.DateKey<>f.DateKey OR d.CarrierKey<>f.CarrierKey OR d.OriginAirportKey<>f.OriginAirportKey'))
    check('FactDelay: positive minutes', scalar('SELECT count(*) FROM FactDelay WHERE DelayMinutes IS NULL OR DelayMinutes<=0'))
    check('FactDelay: flight-reason grain', scalar('SELECT count(*) FROM (SELECT FlightKey,DelayReasonKey FROM FactDelay GROUP BY ALL HAVING count(*)>1)'))
    check('FactFlight: years 2022-2023', scalar('SELECT count(*) FROM FactFlight WHERE DateKey NOT BETWEEN 20220101 AND 20231231'))
    check('FactFlight: scheduled hour/minute', scalar('SELECT count(*) FROM FactFlight WHERE ScheduledDepHour<>floor(ScheduledDepTime/100) OR ScheduledDepHour NOT BETWEEN 0 AND 23 OR ScheduledDepMinute NOT BETWEEN 0 AND 59'))
    check('FactFlight: excess minutes/nulls', scalar('SELECT count(*) FROM FactFlight WHERE Excess_Delay_Minutes IS DISTINCT FROM CASE WHEN ArrDelayMinutes IS NOT NULL THEN greatest(ArrDelayMinutes-15,0) END'))
    check('FactDelay: per-flight arrival reconciliation', scalar('SELECT count(*) FROM FactFlight f LEFT JOIN (SELECT FlightKey,sum(DelayMinutes) AS total_minutes FROM FactDelay GROUP BY FlightKey) d USING(FlightKey) WHERE (f.IsArrDelayed15=1 AND (d.total_minutes IS NULL OR d.total_minutes<>f.ArrDelay)) OR (f.IsArrDelayed15=0 AND d.total_minutes IS NOT NULL)'))
    check('DimDelayReason: five BTS causes', scalar('SELECT count(*) FROM DimDelayReason'), 5)
    check('DimDate: continuous complete calendar', scalar("SELECT count(*) FROM generate_series(DATE '2022-01-01', DATE '2023-12-31', INTERVAL 1 DAY) t(day) LEFT JOIN DimDate d ON d.FullDate=t.day WHERE d.DateKey IS NULL"))
    check('DimAirport: geography/name missing', scalar("SELECT count(*) FROM DimAirport WHERE nullif(trim(AirportName),'') IS NULL OR nullif(trim(State),'') IS NULL OR nullif(trim(Region),'') IS NULL"))
    rel_path = args.directory / 'powerbi_relationships.csv'
    with rel_path.open(encoding='utf-8-sig', newline='') as stream:
        rels = list(csv.DictReader(stream))
    check('relationships: exact canonical roles and directions', [list(r.values()) for r in rels], [[str(v) for v in r] for r in RELATIONSHIPS])
    metrics = con.execute('''SELECT count(*) total_flights,
        count(*) FILTER(WHERE IsCancelled=0 AND IsDiverted=0 AND ArrDelay IS NOT NULL) eligible_arrivals,
        count(*) FILTER(WHERE IsCancelled=0 AND IsDiverted=0 AND ArrDelay IS NOT NULL AND ArrDelay<15) on_time_flights,
        sum(IsArrDelayed15) delayed_flights, sum(IsCancelled) cancelled_flights,
        sum(IsDiverted) diverted_flights,
        avg(DepDelayMinutes) avg_dep_delay_minutes,
        avg(ArrDelayMinutes) avg_arr_delay_minutes,
        sum(Excess_Delay_Minutes) excess_delay_minutes,
        min(DateKey) min_date_key, max(DateKey) max_date_key
        FROM FactFlight''').fetchdf().iloc[0].to_dict()
    metrics = {k: int(v) if k not in ('avg_dep_delay_minutes','avg_arr_delay_minutes') else float(v) for k,v in metrics.items()}
    metrics['otp_rate'] = metrics['on_time_flights'] / metrics['eligible_arrivals']
    metrics['cancellation_rate'] = metrics['cancelled_flights'] / metrics['total_flights']
    metrics['total_delay_cause_minutes'] = scalar('SELECT sum(DelayMinutes) FROM FactDelay')
    causes = con.execute('SELECT r.Code, count(*) AS reason_rows, sum(d.DelayMinutes) AS total_minutes FROM FactDelay d JOIN DimDelayReason r USING(DelayReasonKey) GROUP BY r.Code ORDER BY r.Code').fetchall()
    report = dict(status='PASS' if all(c['passed'] for c in checks) else 'FAIL', checks=checks, tables=tables, metrics=metrics, delay_causes=[dict(code=c,rows=n,minutes=m) for c,n,m in causes], sql_comparison=bool(args.database), dax_runtime_verified=False)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(dict(status=report['status'], checks=len(checks), failures=[c for c in checks if not c['passed']], metrics=metrics), ensure_ascii=False, indent=2))
    if report['status'] != 'PASS':
        raise SystemExit(1)
    manifest = dict(
        scope={"years": [2022, 2023], "min_date_key": metrics["min_date_key"], "max_date_key": metrics["max_date_key"]},
        encoding="utf-8-sig",
        tables=tables,
        relationships_file=rel_path.name,
        relationships=rels,
        validation_report=args.report.name,
    )
    (args.directory / '_manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf8'
    )


if __name__ == '__main__':
    main()
