# -*- coding: utf-8 -*-
"""Build the pre-event variable dictionary (all model features) for 8.4.2 from feature_spec_v14.json.

Each feature gets: family, Vietnamese description, the moment it becomes known (known_time) and source.
Unmapped names raise, so the dictionary can never silently miss a feature.
Usage: python make_variable_dictionary.py [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import re

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get("BI_OUT_DIR", os.path.join(HERE, "..", "outputs"))

GROUP_VI = {"carrier": "hãng", "origin": "sân bay đi", "dest": "sân bay đến", "route": "tuyến",
            "carrier_route": "hãng × tuyến", "origin_hour": "sân bay đi × giờ cất cánh KH",
            "dest_hour": "sân bay đến × giờ hạ cánh KH", "carrier_hour": "hãng × giờ cất cánh KH",
            "carrier_origin": "hãng × sân bay đi", "carrier_dest": "hãng × sân bay đến",
            "origin_month": "sân bay đi × tháng", "carrier_month": "hãng × tháng",
            "origin_weekday": "sân bay đi × thứ", "carrier_weekday": "hãng × thứ",
            "flight_number": "hãng × số hiệu chuyến"}
NET_GROUP = {"nat": "toàn quốc", "car": "cùng hãng", "reg": "cùng vùng sân bay đi", "cr": "cùng hãng × vùng"}
WX = {"precip_sum": "tổng lượng mưa ngày (mm)", "precip_max": "lượng mưa giờ lớn nhất (mm)",
      "wind_max": "gió lớn nhất (km/h)", "gust_max": "gió giật lớn nhất (km/h)",
      "visibility_min": "tầm nhìn nhỏ nhất (m)", "cloud_mean": "mây trung bình (%)",
      "temp_mean": "nhiệt độ trung bình (°C)", "weather_code_max": "mã thời tiết WMO nặng nhất",
      "bad_hours": "số giờ thời tiết xấu (mưa lớn/gió giật/tầm nhìn kém/dông)", "missing": "cờ thiếu dữ liệu thời tiết"}
STATIC = {
    "carrier_code": ("Lịch CRS", "Mã hãng khai thác (categorical)"),
    "origin": ("Lịch CRS", "Mã sân bay đi (categorical)"), "dest": ("Lịch CRS", "Mã sân bay đến (categorical)"),
    "origin_state": ("Lịch CRS", "Bang của sân bay đi"), "dest_state": ("Lịch CRS", "Bang của sân bay đến"),
    "origin_region": ("Lịch CRS", "Vùng địa lý sân bay đi"), "dest_region": ("Lịch CRS", "Vùng địa lý sân bay đến"),
    "season": ("Lịch CRS", "Mùa của ngày bay"), "distance_group": ("Lịch CRS", "Nhóm khoảng cách tuyến"),
    "scheduled_dep_time": ("Lịch CRS", "Giờ cất cánh kế hoạch CRS (hhmm)"),
    "scheduled_dep_hour": ("Lịch CRS", "Giờ cất cánh kế hoạch"), "scheduled_dep_minute": ("Lịch CRS", "Phút cất cánh kế hoạch"),
    "crs_elapsed_time_minutes": ("Lịch CRS", "Thời gian bay kế hoạch CRS (phút)"),
    "crs_elapsed_missing": ("Lịch CRS", "Cờ thiếu thời gian bay kế hoạch"),
    "distance_miles": ("Lịch CRS", "Khoảng cách chuyến (dặm)"), "route_distance": ("Lịch CRS", "Khoảng cách tuyến chuẩn (dặm)"),
    "distance_group_order": ("Lịch CRS", "Thứ tự nhóm khoảng cách"),
    "year": ("Lịch CRS", "Năm"), "quarter": ("Lịch CRS", "Quý"), "month": ("Lịch CRS", "Tháng"),
    "day_of_month": ("Lịch CRS", "Ngày trong tháng"), "day_of_year": ("Lịch CRS", "Ngày trong năm"),
    "week_of_year": ("Lịch CRS", "Tuần trong năm"), "weekday_number": ("Lịch CRS", "Thứ trong tuần"),
    "is_weekend": ("Lịch CRS", "Cờ cuối tuần"),
    "origin_lat": ("Lịch CRS", "Vĩ độ sân bay đi"), "origin_lon": ("Lịch CRS", "Kinh độ sân bay đi"),
    "dest_lat": ("Lịch CRS", "Vĩ độ sân bay đến"), "dest_lon": ("Lịch CRS", "Kinh độ sân bay đến"),
    "dep_minute_of_day": ("Lịch CRS", "Phút trong ngày của giờ cất cánh KH"),
    "sched_arr_minute_of_day": ("Lịch CRS", "Phút trong ngày của giờ hạ cánh KH (suy từ CRS)"),
    "scheduled_arr_hour": ("Lịch CRS", "Giờ hạ cánh kế hoạch"),
    "is_red_eye": ("Lịch CRS", "Cờ chuyến đêm (22h–6h)"), "is_morning_peak": ("Lịch CRS", "Cờ cao điểm sáng (6–9h)"),
    "is_evening_peak": ("Lịch CRS", "Cờ cao điểm chiều (16–20h)"), "same_state": ("Lịch CRS", "Cờ tuyến nội bang"),
    "pace_mph": ("Lịch CRS", "Tốc độ kế hoạch = khoảng cách / thời gian bay KH"),
    "origin_day_flights": ("Lịch CRS", "Số chuyến lịch trong ngày tại sân bay đi"),
    "origin_hour_flights": ("Lịch CRS", "Số chuyến lịch cùng giờ tại sân bay đi"),
    "dest_day_flights": ("Lịch CRS", "Số chuyến lịch trong ngày tới sân bay đến"),
    "route_day_flights": ("Lịch CRS", "Số chuyến lịch trong ngày trên tuyến"),
    "carrier_day_flights": ("Lịch CRS", "Số chuyến lịch trong ngày của hãng"),
    "origin_dep_in_hour": ("Lịch CRS", "Số chuyến cất cánh lịch cùng giờ tại sân bay đi"),
    "dest_arr_in_hour": ("Lịch CRS", "Số chuyến hạ cánh lịch cùng giờ tại sân bay đến"),
    "origin_hub_flights": ("Lịch CRS", "Quy mô hub sân bay đi (số chuyến kỳ train)"),
    "dest_hub_flights": ("Lịch CRS", "Quy mô hub sân bay đến (số chuyến kỳ train)"),
    "wx_bad_hours_both": ("Thời tiết", "Tổng giờ thời tiết xấu tại sân bay đi + đến"),
    "wx_wind_max_both": ("Thời tiết", "Gió lớn nhất giữa hai đầu tuyến"),
    "wx_precip_sum_both": ("Thời tiết", "Tổng mưa hai đầu tuyến"),
    "wx_bad_any": ("Thời tiết", "Cờ có thời tiết xấu ở ít nhất một đầu tuyến"),
    "has_inbound": ("Lịch sử tàu bay", "Có chặng inbound cùng hãng + số hiệu hạ cánh tại sân bay đi trước giờ đi KH"),
    "sched_turn_min": ("Lịch sử tàu bay", "Thời gian quay đầu kế hoạch giữa chặng inbound và chuyến (phút)"),
    "in_status_known": ("Lịch sử tàu bay", "Trạng thái chặng inbound đã biết tại T_pred"),
    "in_dep_delay_known": ("Lịch sử tàu bay", "Độ trễ cất cánh chặng inbound (chỉ khi đã cất cánh trước T_pred)"),
    "in_cancel_known": ("Lịch sử tàu bay", "Chặng inbound bị hủy và đã biết tại T_pred"),
    "in_delay_lb": ("Lịch sử tàu bay", "Cận dưới độ trễ chặng inbound đã biết tại T_pred"),
    "turn_slack_lb": ("Lịch sử tàu bay", "Dư địa quay đầu = quay đầu KH − cận dưới trễ inbound"),
    "o_backlog": ("Nowcast sân bay", "Số chuyến đến giờ đi KH nhưng chưa cất cánh tại sân bay đi"),
    "o_cancel_180": ("Nowcast sân bay", "Số chuyến bị hủy tại sân bay đi trong 180 phút gần nhất"),
    "o_cancel_day": ("Nowcast sân bay", "Số chuyến bị hủy tại sân bay đi từ đầu ngày"),
}
KNOWN = {"Lịch CRS": "Khi lịch CRS được công bố (trước ngày bay)",
         "Lịch sử ≤ D-1": "Cuối ngày D-1 (cửa sổ kết thúc 1 PRECEDING)",
         "Thời tiết": "Dự báo ngày bay (proxy: quan trắc Open-Meteo)",
         "Lịch sử tàu bay": "Sự kiện chặng inbound đã xảy ra ≤ T_pred",
         "Nowcast sân bay": "Sự kiện tại sân bay đã xảy ra ≤ T_pred (giờ địa phương)",
         "Nowcast mạng lưới": "Sự kiện mạng lưới đã xảy ra ≤ T_pred (đồng hồ UTC)"}
SOURCE = {"Lịch CRS": "marts.fact_flight + dim_* (8.1)", "Lịch sử ≤ D-1": "marts.fact_flight (8.1)",
          "Thời tiết": "Open-Meteo archive API", "Lịch sử tàu bay": "intermediate.int_flights_2022_2023",
          "Nowcast sân bay": "intermediate.int_flights_2022_2023", "Nowcast mạng lưới": "intermediate.int_flights_2022_2023 + múi giờ IANA"}


def describe(f: str) -> tuple[str, str]:
    if f in STATIC:
        return STATIC[f]
    if f.endswith(("_sin", "_cos")):
        base = {"month": "tháng", "doy": "ngày trong năm", "weekday": "thứ", "dep_hour": "giờ cất cánh KH"}[f.rsplit("_", 1)[0]]
        return "Lịch CRS", f"Mã hóa chu kỳ {'sin' if f.endswith('sin') else 'cos'} của {base}"
    m = re.fullmatch(r"(origin|dest)_wx_(\w+)", f)
    if m:
        return "Thời tiết", f"{WX[m.group(2)].capitalize()} tại {'sân bay đi' if m.group(1) == 'origin' else 'sân bay đến'}"
    m = re.fullmatch(r"(\w+?)_(prev_day|7d|30d|prior)", f)
    if m and m.group(1) in GROUP_VI:
        w = {"prev_day": "của ngày có dữ liệu gần nhất trước D", "7d": "trong 7 ngày có dữ liệu trước D",
             "30d": "trong 30 ngày có dữ liệu trước D", "prior": "lũy kế trước D (làm trơn Bayes)"}[m.group(2)]
        return "Lịch sử ≤ D-1", f"Tỷ lệ trễ ≥15' theo {GROUP_VI[m.group(1)]} {w}"
    m = re.fullmatch(r"(\w+?)_rate(_n)?", f)
    if m and m.group(1) in GROUP_VI:
        return "Lịch sử ≤ D-1", (f"Số chuyến lịch sử dùng để tính tỷ lệ trễ theo {GROUP_VI[m.group(1)]}" if m.group(2)
                                 else f"Tỷ lệ trễ ≥15' lũy kế trước ngày D theo {GROUP_VI[m.group(1)]} (làm trơn)")
    m = re.fullmatch(r"(fn|route_fn)_(arr_mean|delayed|n|days)", f)
    if m:
        who = "cùng số hiệu" if m.group(1) == "fn" else "cùng số hiệu và tuyến"
        what = {"arr_mean": "trễ đến trung bình (phút)", "delayed": "tỷ lệ trễ ≥15'", "n": "số chuyến",
                "days": "số ngày kể từ"}[m.group(2)]
        return "Lịch sử ≤ D-1", f"Lần khai thác gần nhất trước D của chuyến {who}: {what}"
    m = re.fullmatch(r"(o|d)_(dep|arr)_(n|mean|r15)_(60|180|day)", f)
    if m:
        ap = "sân bay đi" if m.group(1) == "o" else "sân bay đến"
        ev = "cất cánh" if m.group(2) == "dep" else "hạ cánh"
        st = {"n": "số chuyến đã", "mean": "trễ trung bình các chuyến đã", "r15": "tỷ lệ trễ ≥15' các chuyến đã"}[m.group(3)]
        w = "từ đầu ngày" if m.group(4) == "day" else f"trong {m.group(4)} phút"
        return "Nowcast sân bay", f"{st.capitalize()} {ev} tại {ap} {w} trước T_pred"
    m = re.fullmatch(r"(nat|car|reg|cr)_(dep|arr)_(n|mean|r15)_(\d+)", f)
    if m:
        ev = "cất cánh" if m.group(2) == "dep" else "hạ cánh"
        st = {"n": "Số chuyến đã", "mean": "Trễ trung bình các chuyến đã", "r15": "Tỷ lệ trễ ≥15' các chuyến đã"}[m.group(3)]
        return "Nowcast mạng lưới", f"{st} {ev} ({NET_GROUP[m.group(1)]}) trong {m.group(4)} phút trước T_pred"
    m = re.fullmatch(r"(nat|car|reg|cr)_cancel_rate_360", f)
    if m:
        return "Nowcast mạng lưới", f"Tỷ lệ hủy chuyến ({NET_GROUP[m.group(1)]}) trong 360 phút trước T_pred"
    raise KeyError(f"no dictionary rule for feature '{f}'")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=OUT)
    a = p.parse_args()
    spec = json.load(open(os.path.join(a.out, "models", "feature_spec_v14.json"), encoding="utf-8"))
    imp_path = os.path.join(a.out, "feature_importance_v14.csv")
    imp = pd.read_csv(imp_path).set_index("feature")["gain_share"] if os.path.exists(imp_path) else pd.Series(dtype=float)
    rows = []
    for f in spec["features"]:
        fam, desc = describe(f)
        rows.append({"Bien": f, "Nhom": fam, "Mo_ta": desc, "Kieu": "Phân loại" if f in spec["categorical"] else "Số",
                     "Thoi_diem_biet": KNOWN[fam], "Nguon": SOURCE[fam],
                     "Gain_share_pct": round(100 * float(imp.get(f, 0.0)), 3)})
    df = pd.DataFrame(rows)
    out = os.path.join(a.out, "variable_dictionary_pre_event_v14.csv")
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print("wrote", out, len(df), "features")
    print(df.Nhom.value_counts().to_string())


if __name__ == "__main__":
    main()
