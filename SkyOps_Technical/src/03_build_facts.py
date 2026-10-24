"""Build FactFlight and melted FactDelay against the six existing dimensions."""

from __future__ import annotations

import numpy as np
import pandas as pd

from etl_common import (
    BUSINESS_KEY, RAW_COLUMNS, REASON_COLUMNS, SCHEMAS, arguments, load_dimensions, map_key,
    read_flights, require_foreign_key, require_nonnull, require_unique, write_table,
)


def build_flights(flights: pd.DataFrame, dimensions: dict[str, pd.DataFrame]) -> pd.DataFrame:
    require_unique(flights, BUSINESS_KEY, "Flight business key")
    require_nonnull(flights, ["crs_dep_time"], "Scheduled departure")
    for flag in ("cancelled", "diverted"):
        if not flights[flag].isin([0, 1]).all():
            raise ValueError(f"Flight input: {flag} must be 0 or 1")

    fact = pd.DataFrame(index=flights.index)
    fact["flight_key"] = np.arange(1, len(flights) + 1, dtype="int64")
    fact["date_key"] = flights["flight_date"].dt.strftime("%Y%m%d").astype("int64")
    fact["carrier_key"] = map_key(flights["carrier_code"], dimensions["DimCarrier"], "carrier_code", "carrier_key")
    for role in ("origin", "dest"):
        fact[f"{role}_airport_key"] = map_key(
            flights[f"{role}_code"], dimensions["DimAirport"], "airport_code", "airport_key",
        )
    routes = dimensions["DimRoute"]
    route_index = pd.MultiIndex.from_frame(flights[["origin_code", "dest_code"]])
    route_keys = routes.set_index(["origin_code", "dest_code"])["route_key"].reindex(route_index)
    if route_keys.isna().any():
        raise ValueError(f"Missing DimRoute members: {route_keys.loc[route_keys.isna()].index.unique().tolist()[:10]}")
    fact["route_key"] = route_keys.to_numpy(dtype="int64")

    cancelled = flights["cancelled"].eq(1)
    diverted = flights["diverted"].eq(1)
    completed = ~cancelled & ~diverted
    status = pd.Series("On-time", index=flights.index, dtype="string")
    status.loc[flights["arr_delay"].ge(15)] = "Delayed"
    status.loc[diverted] = "Diverted"
    status.loc[cancelled] = "Cancelled"
    fact["flight_status_key"] = map_key(status, dimensions["DimFlightStatus"], "flight_status_code", "flight_status_key")
    fact["flight_number"] = flights["flight_number"].astype("int64")
    scheduled = flights["crs_dep_time"].mask(flights["crs_dep_time"].eq(2400), 0).astype("int64")
    fact["scheduled_dep_time"] = scheduled
    fact["scheduled_dep_hour"] = scheduled // 100
    fact["scheduled_dep_minute"] = scheduled % 100
    fact["cancellation_code"] = flights["cancellation_code"]
    fact["dep_delay"] = flights["dep_delay"]
    fact["arr_delay"] = flights["arr_delay"]
    fact["dep_delay_minutes"] = flights["dep_delay"].clip(lower=0).where(~cancelled)
    fact["arr_delay_minutes"] = flights["arr_delay"].clip(lower=0).where(completed)
    fact["excess_delay_minutes"] = (fact["arr_delay_minutes"] - 15).clip(lower=0)
    for column in ("taxi_out", "taxi_in", "air_time", "elapsed_time", "crs_elapsed_time"):
        fact[f"{column}_minutes"] = flights[column]
    fact["distance_miles"] = flights["distance"]
    fact["is_dep_delayed15"] = (~cancelled & flights["dep_delay"].ge(15)).astype("int64")
    fact["is_arr_delayed15"] = (completed & flights["arr_delay"].ge(15)).astype("int64")
    fact["is_cancelled"] = flights["cancelled"].astype("int64")
    fact["is_diverted"] = flights["diverted"].astype("int64")
    fact["is_completed"] = completed.astype("int64")
    fact["has_delay_reason"] = flights[list(REASON_COLUMNS)].fillna(0).gt(0).any(axis=1).astype("int64")

    for column, dimension, key in [
        ("date_key", "DimDate", "date_key"),
        ("carrier_key", "DimCarrier", "carrier_key"),
        ("origin_airport_key", "DimAirport", "airport_key"),
        ("dest_airport_key", "DimAirport", "airport_key"),
        ("route_key", "DimRoute", "route_key"),
        ("flight_status_key", "DimFlightStatus", "flight_status_key"),
    ]:
        require_foreign_key(fact[column], dimensions[dimension], key, "FactFlight")
    for role in ("origin", "dest"):
        require_foreign_key(routes[f"{role}_airport_key"], dimensions["DimAirport"], "airport_key", "DimRoute")
        expected = map_key(routes[f"{role}_code"], dimensions["DimAirport"], "airport_code", "airport_key")
        if not routes[f"{role}_airport_key"].eq(expected).all():
            raise ValueError(f"DimRoute: inconsistent {role} airport mapping")
    return fact[SCHEMAS["FactFlight"]]


def build_delays(
    flights: pd.DataFrame, fact: pd.DataFrame, dimensions: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    identifiers = ["flight_key", "date_key", "carrier_key", "origin_airport_key"]
    # Zero-only rows cannot survive the positive-minute filter; exclude them before melt.
    candidates = fact["has_delay_reason"].eq(1)
    wide = pd.concat([
        fact.loc[candidates, identifiers], flights.loc[candidates, list(REASON_COLUMNS)],
    ], axis=1)
    delays = wide.melt(
        id_vars=identifiers, value_vars=list(REASON_COLUMNS),
        var_name="source_column", value_name="delay_minutes",
    )
    delays = delays.loc[delays["delay_minutes"].gt(0)].copy()
    delays["delay_reason_code"] = delays["source_column"].map(REASON_COLUMNS)
    delays["delay_reason_key"] = map_key(
        delays["delay_reason_code"], dimensions["DimDelayReason"], "delay_reason_code", "delay_reason_key",
    )
    delays.sort_values(["flight_key", "delay_reason_code"], inplace=True, ignore_index=True)
    delays.insert(0, "delay_fact_key", np.arange(1, len(delays) + 1, dtype="int64"))
    require_unique(delays, ["flight_key", "delay_reason_key"], "FactDelay grain")
    require_foreign_key(delays["flight_key"], fact, "flight_key", "FactDelay lineage")
    for column, dimension, key in [
        ("date_key", "DimDate", "date_key"),
        ("carrier_key", "DimCarrier", "carrier_key"),
        ("origin_airport_key", "DimAirport", "airport_key"),
        ("delay_reason_key", "DimDelayReason", "delay_reason_key"),
    ]:
        require_foreign_key(delays[column], dimensions[dimension], key, "FactDelay")
    return delays[SCHEMAS["FactDelay"]]


def main() -> None:
    args = arguments(__doc__)
    dimensions = load_dimensions(args.output_dir)
    fact_columns = [
        column for column in RAW_COLUMNS.values()
        if column not in {"carrier_name", "airline_dot", "dot_code"}
    ]
    flights = read_flights(args.raw, args.chunksize, fact_columns)
    flights.sort_values(BUSINESS_KEY, inplace=True, ignore_index=True)
    fact = build_flights(flights, dimensions)
    delays = build_delays(flights, fact, dimensions)
    write_table(fact, "FactFlight", args.output_dir)
    write_table(delays, "FactDelay", args.output_dir)


if __name__ == "__main__":
    main()
