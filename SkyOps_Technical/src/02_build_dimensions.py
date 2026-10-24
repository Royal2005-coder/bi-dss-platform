"""Build six handoff dimensions using the committed dbt model contracts."""

from __future__ import annotations

import numpy as np
import pandas as pd

from etl_common import (
    AIRPORT_SEED, DIMENSION_KEYS, END_DATE, SCHEMAS, START_DATE, arguments,
    map_key, read_flights, require_unique, sql_round_2, write_table,
)


def build_dates() -> pd.DataFrame:
    dates = pd.Series(pd.date_range(START_DATE, END_DATE, freq="D"))
    month = dates.dt.month
    weekday = dates.dt.dayofweek + 1
    frame = pd.DataFrame({
        "date_key": dates.dt.strftime("%Y%m%d").astype("int64"),
        "full_date": dates,
        "year": dates.dt.year,
        "quarter": dates.dt.quarter,
        "quarter_name": "Q" + dates.dt.quarter.astype(str),
        "month": month,
        "month_name": "Tháng " + month.astype(str),
        "month_year": dates.dt.strftime("%Y-%m"),
        "day_of_month": dates.dt.day,
        "day_of_year": dates.dt.dayofyear,
        "week_of_year": dates.dt.isocalendar().week.astype("int64"),
        "weekday_number": weekday,
        "weekday_name": weekday.map({
            1: "Thứ Hai", 2: "Thứ Ba", 3: "Thứ Tư", 4: "Thứ Năm",
            5: "Thứ Sáu", 6: "Thứ Bảy", 7: "Chủ Nhật",
        }),
        "is_weekend": weekday.isin([6, 7]),
        "season": np.select(
            [month.isin([3, 4, 5]), month.isin([6, 7, 8]), month.isin([9, 10, 11])],
            ["Xuân", "Hạ", "Thu"], default="Đông",
        ),
    })
    return frame[SCHEMAS["DimDate"]]


def build_carriers(flights: pd.DataFrame) -> pd.DataFrame:
    attributes = ["carrier_name", "dot_code", "airline_dot"]
    distinct = flights.groupby("carrier_code", dropna=False)[attributes].nunique(dropna=True)
    ambiguous = distinct.gt(1).any(axis=1)
    if ambiguous.any():
        raise ValueError(f"Ambiguous carrier attributes: {distinct.index[ambiguous].tolist()}")
    carriers = (
        flights.groupby("carrier_code", sort=True, as_index=False)[attributes]
        .min().sort_values("carrier_code", ignore_index=True)
    )
    carriers.insert(0, "carrier_key", np.arange(1, len(carriers) + 1, dtype="int64"))
    carriers["dot_code"] = carriers["dot_code"].astype("Int64")
    return carriers[SCHEMAS["DimCarrier"]]


def build_airports(flights: pd.DataFrame) -> pd.DataFrame:
    codes = pd.concat([flights["origin_code"], flights["dest_code"]], ignore_index=True)
    airports = codes.drop_duplicates().sort_values(ignore_index=True).to_frame("airport_code")
    airports.insert(0, "airport_key", np.arange(1, len(airports) + 1, dtype="int64"))
    seed = pd.read_csv(
        AIRPORT_SEED, dtype={"airport_code": "string"},
        keep_default_na=False, na_values=[""], encoding="utf-8-sig", float_precision="round_trip",
    )
    require_unique(seed, ["airport_code"], "Committed airport seed")
    airports = airports.merge(seed, on="airport_code", how="left", validate="one_to_one", sort=False)
    airports["airport_name"] = airports["airport_name"].fillna(airports["airport_code"])
    for column, fallback in {
        "city": "", "state_code": "", "state_name": "", "region": "Other",
        "region_name": "Khác", "country_code": "US", "data_source": "missing",
    }.items():
        airports[column] = airports[column].fillna(fallback)
    return airports[SCHEMAS["DimAirport"]]


def build_routes(flights: pd.DataFrame, airports: pd.DataFrame) -> pd.DataFrame:
    routes = (
        flights.groupby(["origin_code", "dest_code"], sort=True, as_index=False)["distance"]
        .mean().rename(columns={"distance": "distance_miles"})
        .sort_values(["origin_code", "dest_code"], ignore_index=True)
    )
    routes["distance_miles"] = sql_round_2(routes["distance_miles"])
    routes.insert(0, "route_key", np.arange(1, len(routes) + 1, dtype="int64"))
    routes["route_name"] = routes["origin_code"] + "-" + routes["dest_code"]
    routes["origin_airport_key"] = map_key(routes["origin_code"], airports, "airport_code", "airport_key")
    routes["dest_airport_key"] = map_key(routes["dest_code"], airports, "airport_code", "airport_key")
    conditions = [routes["distance_miles"].lt(bound) for bound in (500, 1000, 1500, 2000)]
    routes["distance_group"] = np.select(
        conditions, ["0–499 dặm", "500–999 dặm", "1000–1499 dặm", "1500–1999 dặm"],
        default="2000+ dặm",
    )
    routes["distance_group_order"] = np.select(conditions, [1, 2, 3, 4], default=5)
    return routes[SCHEMAS["DimRoute"]]


def build_delay_reasons() -> pd.DataFrame:
    rows = [
        (1, "CARRIER", "Hãng hàng không", "Chậm do hãng vận chuyển (Carrier Delay)", "DELAY_DUE_CARRIER", 1),
        (2, "WEATHER", "Thời tiết", "Chậm do điều kiện thời tiết cực đoan (Weather Delay)", "DELAY_DUE_WEATHER", 2),
        (3, "NAS", "Hệ thống không lưu", "Chậm do hệ thống kiểm soát không lưu (NAS Delay)", "DELAY_DUE_NAS", 3),
        (4, "SECURITY", "An ninh", "Chậm do an ninh hàng không (Security Delay)", "DELAY_DUE_SECURITY", 4),
        (5, "LATE_AIRCRAFT", "Tàu bay đến muộn", "Chậm do tàu bay của chuyến trước đến muộn (Late Aircraft Delay)", "DELAY_DUE_LATE_AIRCRAFT", 5),
    ]
    return pd.DataFrame(rows, columns=SCHEMAS["DimDelayReason"])


def build_flight_statuses() -> pd.DataFrame:
    rows = [
        (1, "On-time", "Đúng giờ", "Chuyến bay hạ cánh sớm hơn hoặc chậm dưới 15 phút so với lịch", True, False, 1),
        (2, "Delayed", "Chậm chuyến", "Chuyến bay hạ cánh chậm từ 15 phút trở lên so với lịch", True, True, 2),
        (3, "Cancelled", "Hủy chuyến", "Chuyến bay bị hủy và không thực hiện hành trình", False, True, 3),
        (4, "Diverted", "Chuyển hướng", "Chuyến bay chuyển hướng, không hạ cánh tại sân bay dự kiến", False, True, 4),
    ]
    return pd.DataFrame(rows, columns=SCHEMAS["DimFlightStatus"])


def main() -> None:
    args = arguments(__doc__)
    flights = read_flights(args.raw, args.chunksize, [
        "flight_date", "carrier_code", "carrier_name", "dot_code", "airline_dot",
        "origin_code", "dest_code", "distance",
    ])
    airports = build_airports(flights)
    dimensions = {
        "DimDate": build_dates(),
        "DimCarrier": build_carriers(flights),
        "DimAirport": airports,
        "DimRoute": build_routes(flights, airports),
        "DimDelayReason": build_delay_reasons(),
        "DimFlightStatus": build_flight_statuses(),
    }
    for name, frame in dimensions.items():
        key, natural_key = DIMENSION_KEYS[name]
        require_unique(frame, [key], name)
        require_unique(frame, natural_key, name)
    for name, frame in dimensions.items():
        write_table(frame, name, args.output_dir)


if __name__ == "__main__":
    main()
