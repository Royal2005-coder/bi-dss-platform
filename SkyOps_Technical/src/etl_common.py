"""Shared contracts and chunked input for the canonical 2022–2023 CSV handoff."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
TECHNICAL_ROOT = REPO_ROOT / "SkyOps_Technical"
DEFAULT_RAW = TECHNICAL_ROOT / "data" / "raw" / "flights_sample_3m.csv"
DEFAULT_OUTPUT = TECHNICAL_ROOT / "data" / "processed"
AIRPORT_SEED = REPO_ROOT / "analytics" / "seeds" / "airport_master.csv"
START_DATE = pd.Timestamp("2022-01-01")
END_DATE = pd.Timestamp("2023-12-31")

RAW_COLUMNS = {
    "FL_DATE": "flight_date",
    "AIRLINE": "carrier_name",
    "AIRLINE_DOT": "airline_dot",
    "AIRLINE_CODE": "carrier_code",
    "DOT_CODE": "dot_code",
    "FL_NUMBER": "flight_number",
    "ORIGIN": "origin_code",
    "DEST": "dest_code",
    "CRS_DEP_TIME": "crs_dep_time",
    "DEP_DELAY": "dep_delay",
    "ARR_DELAY": "arr_delay",
    "CANCELLED": "cancelled",
    "CANCELLATION_CODE": "cancellation_code",
    "DIVERTED": "diverted",
    "TAXI_OUT": "taxi_out",
    "TAXI_IN": "taxi_in",
    "AIR_TIME": "air_time",
    "ELAPSED_TIME": "elapsed_time",
    "CRS_ELAPSED_TIME": "crs_elapsed_time",
    "DISTANCE": "distance",
    "DELAY_DUE_CARRIER": "delay_due_carrier",
    "DELAY_DUE_WEATHER": "delay_due_weather",
    "DELAY_DUE_NAS": "delay_due_nas",
    "DELAY_DUE_SECURITY": "delay_due_security",
    "DELAY_DUE_LATE_AIRCRAFT": "delay_due_late_aircraft",
}
STRING_COLUMNS = {
    "carrier_code", "carrier_name", "airline_dot", "origin_code", "dest_code",
    "cancellation_code",
}
INTEGER_COLUMNS = {"dot_code", "flight_number", "crs_dep_time", "cancelled", "diverted"}
BUSINESS_KEY = ["flight_date", "carrier_code", "flight_number", "origin_code", "dest_code"]
REASON_COLUMNS = {
    "delay_due_carrier": "CARRIER",
    "delay_due_weather": "WEATHER",
    "delay_due_nas": "NAS",
    "delay_due_security": "SECURITY",
    "delay_due_late_aircraft": "LATE_AIRCRAFT",
}
ALIASES = {
    "state_code": "State",
    "flight_status_code": "StatusCode",
    "flight_status_name": "StatusName",
    "delay_reason_code": "Code",
    "delay_reason_name": "ReasonName",
    "excess_delay_minutes": "Excess_Delay_Minutes",
}
SCHEMAS = {
    "DimDate": [
        "date_key", "full_date", "year", "quarter", "quarter_name", "month",
        "month_name", "month_year", "day_of_month", "day_of_year", "week_of_year",
        "weekday_number", "weekday_name", "is_weekend", "season",
    ],
    "DimCarrier": ["carrier_key", "carrier_code", "carrier_name", "dot_code", "airline_dot"],
    "DimAirport": [
        "airport_key", "airport_code", "airport_name", "city", "state_code",
        "state_name", "region", "region_name", "country_code", "latitude",
        "longitude", "data_source",
    ],
    "DimRoute": [
        "route_key", "origin_code", "dest_code", "route_name", "origin_airport_key",
        "dest_airport_key", "distance_miles", "distance_group", "distance_group_order",
    ],
    "DimDelayReason": [
        "delay_reason_key", "delay_reason_code", "delay_reason_name", "description",
        "source_column", "sort_order",
    ],
    "DimFlightStatus": [
        "flight_status_key", "flight_status_code", "flight_status_name", "description",
        "is_completed", "is_disrupted", "sort_order",
    ],
    "FactFlight": [
        "flight_key", "date_key", "carrier_key", "origin_airport_key", "dest_airport_key",
        "route_key", "flight_status_key", "flight_number", "scheduled_dep_time",
        "scheduled_dep_hour", "scheduled_dep_minute", "cancellation_code", "dep_delay",
        "arr_delay", "dep_delay_minutes", "arr_delay_minutes", "excess_delay_minutes",
        "taxi_out_minutes", "taxi_in_minutes", "air_time_minutes", "elapsed_time_minutes",
        "crs_elapsed_time_minutes", "distance_miles", "is_dep_delayed15",
        "is_arr_delayed15", "is_cancelled", "is_diverted", "is_completed", "has_delay_reason",
    ],
    "FactDelay": [
        "delay_fact_key", "flight_key", "date_key", "carrier_key", "origin_airport_key",
        "delay_reason_key", "delay_minutes",
    ],
}
DIMENSION_KEYS = {
    "DimDate": ("date_key", ["full_date"]),
    "DimCarrier": ("carrier_key", ["carrier_code"]),
    "DimAirport": ("airport_key", ["airport_code"]),
    "DimRoute": ("route_key", ["origin_code", "dest_code"]),
    "DimDelayReason": ("delay_reason_key", ["delay_reason_code"]),
    "DimFlightStatus": ("flight_status_key", ["flight_status_code"]),
}


def external_name(column: str) -> str:
    return ALIASES.get(column, "".join(token.capitalize() for token in column.split("_")))


def arguments(description: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW, help="Source flight CSV (never copied into the repository).")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT, help="Directory containing the eight handoff CSVs.")
    parser.add_argument("--chunksize", type=int, default=100_000, help="Maximum raw rows parsed per input chunk.")
    args = parser.parse_args()
    if args.chunksize <= 0:
        parser.error("--chunksize must be positive")
    return args


def require_nonnull(frame: pd.DataFrame, columns: Sequence[str], label: str) -> None:
    missing = frame[list(columns)].isna().sum()
    missing = missing[missing > 0]
    if not missing.empty:
        raise ValueError(f"{label}: missing required values: {missing.to_dict()}")


def require_unique(frame: pd.DataFrame, columns: Sequence[str], label: str) -> None:
    require_nonnull(frame, columns, label)
    duplicates = frame.duplicated(list(columns), keep=False)
    if duplicates.any():
        examples = frame.loc[duplicates, list(columns)].head(5).to_dict("records")
        raise ValueError(f"{label}: duplicate or ambiguous mapping for {list(columns)}: {examples}")


def read_flights(raw: Path, chunksize: int, columns: Sequence[str] | None = None) -> pd.DataFrame:
    """Parse bounded chunks and discard out-of-scope dates before concatenating."""
    selected = list(columns) if columns is not None else list(RAW_COLUMNS.values())
    if "flight_date" not in selected:
        selected.insert(0, "flight_date")
    raw_names = {value: key for key, value in RAW_COLUMNS.items()}
    usecols = [raw_names[column] for column in selected]
    dtypes = {
        raw_names[column]: "string" if column in STRING_COLUMNS or column == "flight_date" else "float64"
        for column in selected
    }
    retained = []
    for chunk in pd.read_csv(
        raw, usecols=usecols, dtype=dtypes, chunksize=chunksize,
        keep_default_na=False, na_values=[""], encoding="utf-8-sig",
    ):
        chunk.rename(columns=RAW_COLUMNS, inplace=True)
        dates = pd.to_datetime(chunk["flight_date"], errors="raise", format="mixed").dt.normalize()
        in_scope = dates.between(START_DATE, END_DATE)
        if not in_scope.any():
            continue
        chunk = chunk.loc[in_scope].copy()
        chunk["flight_date"] = dates.loc[in_scope]
        for column in STRING_COLUMNS.intersection(selected):
            chunk[column] = chunk[column].str.strip()
        if "cancellation_code" in chunk:
            chunk["cancellation_code"] = chunk["cancellation_code"].replace("", pd.NA)
        for column in INTEGER_COLUMNS.intersection(selected):
            if column in {"cancelled", "diverted"}:
                chunk[column] = chunk[column].fillna(0)
            chunk[column] = chunk[column].astype("Int64")
        retained.append(chunk)
    if not retained:
        raise ValueError(f"{raw}: no flights dated 2022-01-01 through 2023-12-31")
    flights = pd.concat(retained, ignore_index=True, copy=False)
    require_nonnull(flights, [c for c in BUSINESS_KEY if c in flights], "Flight input")
    return flights


def write_table(frame: pd.DataFrame, name: str, output_dir: Path) -> None:
    expected = SCHEMAS[name]
    if list(frame.columns) != expected:
        raise ValueError(f"{name}: unexpected schema {list(frame.columns)}; expected {expected}")
    key = DIMENSION_KEYS[name][0] if name in DIMENSION_KEYS else expected[0]
    require_unique(frame, [key], name)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{name}.csv"
    frame.rename(columns=external_name).to_csv(path, index=False, encoding="utf-8-sig", date_format="%Y-%m-%d")
    print(f"{name}: {len(frame):,} rows -> {path}")


def load_dimensions(output_dir: Path) -> dict[str, pd.DataFrame]:
    """Require all six existing dimensions rather than silently rebuilding keys."""
    missing = [f"{name}.csv" for name in DIMENSION_KEYS if not (output_dir / f"{name}.csv").is_file()]
    if missing:
        raise FileNotFoundError(f"Run 02_build_dimensions.py first; missing in {output_dir}: {', '.join(missing)}")
    dimensions = {}
    text_columns = {
        "full_date", "carrier_code", "carrier_name", "airline_dot", "airport_code",
        "airport_name", "city", "state_code", "state_name", "region", "region_name",
        "country_code", "data_source", "origin_code", "dest_code", "route_name",
        "distance_group", "delay_reason_code", "delay_reason_name", "description",
        "source_column", "flight_status_code", "flight_status_name", "quarter_name",
        "month_name", "month_year", "weekday_name", "season",
    }
    for name, (key, natural_key) in DIMENSION_KEYS.items():
        schema = SCHEMAS[name]
        frame = pd.read_csv(
            output_dir / f"{name}.csv", keep_default_na=False, na_values=[""],
            dtype={external_name(c): "string" for c in schema if c in text_columns},
        )
        expected = [external_name(c) for c in schema]
        if list(frame.columns) != expected:
            raise ValueError(f"{name}: expected columns {expected}; got {list(frame.columns)}")
        frame.columns = schema
        require_unique(frame, [key], name)
        require_unique(frame, natural_key, name)
        frame[key] = frame[key].astype("int64")
        dimensions[name] = frame
    return dimensions


def map_key(values: pd.Series, dimension: pd.DataFrame, natural_key: str, key: str) -> pd.Series:
    require_unique(dimension, [natural_key], key)
    result = values.map(dimension.set_index(natural_key)[key])
    if result.isna().any():
        missing = values.loc[result.isna()].drop_duplicates().head(10).tolist()
        raise ValueError(f"{key}: missing dimension members {missing}")
    return result.astype("int64")


def require_foreign_key(values: pd.Series, dimension: pd.DataFrame, key: str, label: str) -> None:
    invalid = values.isna() | ~values.isin(dimension[key])
    if invalid.any():
        raise ValueError(f"{label}: missing {key}: {values.loc[invalid].drop_duplicates().head(10).tolist()}")


def sql_round_2(values: pd.Series) -> pd.Series:
    """DuckDB ROUND uses half-away-from-zero, unlike pandas' ties-to-even."""
    return np.sign(values) * np.floor(np.abs(values) * 100 + 0.5) / 100
