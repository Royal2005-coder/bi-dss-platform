#!/usr/bin/env python3
"""Build airport_master.csv from the raw flight CSV and OurAirports."""
import csv
import os
import sys
from collections import defaultdict

BASE = os.environ.get("BI_BASE", "/workspace")
RAW = os.path.join(BASE, "data", "flights_sample_3m.csv")
OURAIRPORTS = os.path.join(BASE, "data", "airports.csv")
OUT = os.path.join(BASE, "seeds", "airport_master.csv")

STATES = {
    "AL": ("Alabama", "South"), "AK": ("Alaska", "West"), "AZ": ("Arizona", "West"),
    "AR": ("Arkansas", "South"), "CA": ("California", "West"), "CO": ("Colorado", "West"),
    "CT": ("Connecticut", "Northeast"), "DE": ("Delaware", "South"),
    "DC": ("District of Columbia", "South"), "FL": ("Florida", "South"),
    "GA": ("Georgia", "South"), "HI": ("Hawaii", "West"), "ID": ("Idaho", "West"),
    "IL": ("Illinois", "Midwest"), "IN": ("Indiana", "Midwest"), "IA": ("Iowa", "Midwest"),
    "KS": ("Kansas", "Midwest"), "KY": ("Kentucky", "South"), "LA": ("Louisiana", "South"),
    "ME": ("Maine", "Northeast"), "MD": ("Maryland", "South"),
    "MA": ("Massachusetts", "Northeast"), "MI": ("Michigan", "Midwest"),
    "MN": ("Minnesota", "Midwest"), "MS": ("Mississippi", "South"),
    "MO": ("Missouri", "Midwest"), "MT": ("Montana", "West"), "NE": ("Nebraska", "Midwest"),
    "NV": ("Nevada", "West"), "NH": ("New Hampshire", "Northeast"),
    "NJ": ("New Jersey", "Northeast"), "NM": ("New Mexico", "West"),
    "NY": ("New York", "Northeast"), "NC": ("North Carolina", "South"),
    "ND": ("North Dakota", "Midwest"), "OH": ("Ohio", "Midwest"),
    "OK": ("Oklahoma", "South"), "OR": ("Oregon", "West"),
    "PA": ("Pennsylvania", "Northeast"), "RI": ("Rhode Island", "Northeast"),
    "SC": ("South Carolina", "South"), "SD": ("South Dakota", "Midwest"),
    "TN": ("Tennessee", "South"), "TX": ("Texas", "South"), "UT": ("Utah", "West"),
    "VT": ("Vermont", "Northeast"), "VA": ("Virginia", "South"),
    "WA": ("Washington", "West"), "WV": ("West Virginia", "South"),
    "WI": ("Wisconsin", "Midwest"), "WY": ("Wyoming", "West"),
    "PR": ("Puerto Rico", "South"), "VI": ("U.S. Virgin Islands", "South"),
    "GU": ("Guam", "West"), "AS": ("American Samoa", "West"),
    "MP": ("Northern Mariana Islands", "West"),
}
REGION_VI = {
    "Northeast": "Đông Bắc",
    "Midwest": "Trung Tây",
    "South": "Nam",
    "West": "Tây",
    "Other": "Khác",
}
TERRITORIES = {
    "PR": ("PR", "Puerto Rico", "South"),
    "VI": ("VI", "U.S. Virgin Islands", "South"),
    "GU": ("GU", "Guam", "West"),
    "AS": ("AS", "American Samoa", "West"),
    "MP": ("MP", "Northern Mariana Islands", "West"),
}
# OurAirports 2026 already carries the PBI -> DJT rename and no longer lists ISN.
MANUAL = {
    "PBI": {
        "airport_name": "Palm Beach International Airport",
        "city": "West Palm Beach",
        "state_code": "FL",
        "country_code": "US",
        "latitude": "26.683201",
        "longitude": "-80.095596",
    },
    "ISN": {
        "airport_name": "Williston Basin International Airport",
        "city": "Williston",
        "state_code": "ND",
        "country_code": "US",
        "latitude": "48.2594",
        "longitude": "-103.7510",
    },
}


def parse_city(value):
    value = (value or "").strip()
    if "," in value:
        city, tail = value.rsplit(",", 1)
        return city.strip(), tail.strip().upper()
    return value, ""


def main():
    wanted = defaultdict(set)
    with open(RAW, newline="", encoding="utf8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            for code_col, city_col in (("ORIGIN", "ORIGIN_CITY"), ("DEST", "DEST_CITY")):
                code = (row.get(code_col) or "").strip().upper()
                if code:
                    wanted[code].add((row.get(city_col) or "").strip())
    print("airport codes in raw:", len(wanted))

    ourairports = {}
    if os.path.exists(OURAIRPORTS):
        with open(OURAIRPORTS, newline="", encoding="utf8") as fh:
            for row in csv.DictReader(fh):
                if (row.get("iso_country") or "") not in {
                    "US", "PR", "VI", "GU", "AS", "MP"
                }:
                    continue
                code = (row.get("iata_code") or "").strip().upper()
                if not code:
                    continue
                country = (row.get("iso_country") or "").strip().upper()
                region = (row.get("iso_region") or "").strip()
                if country in TERRITORIES:
                    state_code, state_name, region_name = TERRITORIES[country]
                elif country == "US" and region.startswith("US-"):
                    state_code = region[3:].upper()
                    state_name, region_name = STATES.get(state_code, ("", "Other"))
                else:
                    state_code, state_name, region_name = "", "", "Other"
                lat = row.get("latitude_deg") or ""
                lon = row.get("longitude_deg") or ""
                ourairports[code] = {
                    "airport_name": (row.get("name") or "").strip(),
                    "city": (row.get("municipality") or "").strip(),
                    "state_code": state_code,
                    "state_name": state_name,
                    "region": region_name,
                    "country_code": country,
                    "latitude": lat,
                    "longitude": lon,
                }

    rows = []
    missing = []
    for code in sorted(wanted):
        cities = sorted(wanted[code])
        city_from_raw, state_from_raw = parse_city(cities[0] if cities else "")
        if code in MANUAL:
            m = MANUAL[code]
            state_code = m["state_code"]
            state_name, region = STATES.get(state_code, ("", "Other"))
            rows.append({
                "airport_code": code,
                "airport_name": m["airport_name"],
                "city": m["city"],
                "state_code": state_code,
                "state_name": state_name,
                "region": region,
                "region_name": REGION_VI.get(region, region),
                "country_code": m["country_code"],
                "latitude": m["latitude"],
                "longitude": m["longitude"],
                "data_source": "manual",
            })
            continue
        oa = ourairports.get(code)
        if oa:
            city = oa["city"] or city_from_raw
            state_code = oa["state_code"] or state_from_raw
            state_name = oa["state_name"] or STATES.get(state_code, ("", "Other"))[0]
            region = oa["region"] if oa["region"] != "Other" else STATES.get(state_code, ("", "Other"))[1]
            rows.append({
                "airport_code": code,
                "airport_name": oa["airport_name"] or code,
                "city": city,
                "state_code": state_code,
                "state_name": state_name,
                "region": region,
                "region_name": REGION_VI.get(region, region),
                "country_code": oa["country_code"] or "US",
                "latitude": oa["latitude"],
                "longitude": oa["longitude"],
                "data_source": "ourairports",
            })
        else:
            missing.append(code)
            state_name, region = STATES.get(state_from_raw, ("", "Other"))
            rows.append({
                "airport_code": code,
                "airport_name": code,
                "city": city_from_raw,
                "state_code": state_from_raw,
                "state_name": state_name,
                "region": region,
                "region_name": REGION_VI.get(region, region),
                "country_code": "US",
                "latitude": "",
                "longitude": "",
                "data_source": "fallback_raw_city",
            })

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    cols = list(rows[0].keys())
    with open(OUT, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        writer.writerows(rows)

    missing_state = [r["airport_code"] for r in rows if not r["state_code"]]
    print("wrote", OUT, "rows:", len(rows))
    print("no OurAirports match:", len(missing), missing[:30])
    print("no state:", len(missing_state), missing_state[:30])
    print("regions:", {r: sum(1 for x in rows if x["region"] == r) for r in sorted({x["region"] for x in rows})})
    if missing_state:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
