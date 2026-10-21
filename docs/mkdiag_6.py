import itertools
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))
FIG = os.path.join(BASE, "figures")
os.makedirs(FIG, exist_ok=True)
ids = itertools.count(1)


def base(t, x, y, w, h, **k):
    n = next(ids)
    e = {
        "id": f"e{n}", "type": t, "x": x, "y": y, "width": w, "height": h, "angle": 0,
        "strokeColor": k.get("stroke", "#1e1e1e"),
        "backgroundColor": k.get("bg", "transparent"),
        "fillStyle": "solid", "strokeWidth": k.get("sw", 2),
        "strokeStyle": "solid", "roughness": 0, "opacity": 100,
        "groupIds": [], "frameId": None, "roundness": k.get("round"),
        "seed": n * 7919, "version": 1, "versionNonce": n * 104729,
        "isDeleted": False, "boundElements": None, "updated": 1,
        "link": None, "locked": False,
    }
    return e


def box(x, y, w, h, bg, stroke, dashed=False):
    e = base("rectangle", x, y, w, h, bg=bg, stroke=stroke, round={"type": 3})
    if dashed:
        e["strokeStyle"] = "dashed"
    return e


def text(x, y, s, size=16, color="#1e1e1e", w=None, align="left", bold=False):
    lines = s.split("\n")
    cw = size * 0.56
    tw = w or max(len(l) for l in lines) * cw
    th = len(lines) * size * 1.25
    e = base("text", x, y, tw, th, stroke=color)
    e.update({
        "text": s, "fontSize": size, "fontFamily": 2, "textAlign": align,
        "verticalAlign": "top", "baseline": int(size * 0.9),
        "containerId": None, "originalText": s, "lineHeight": 1.25, "autoResize": True,
    })
    return e


def card(x, y, w, h, bg, st, title, body, tsize=17, bsize=13):
    return [
        box(x, y, w, h, bg, st),
        text(x + 14, y + 12, title, tsize, st, w - 28),
        text(x + 14, y + 12 + tsize * 1.25 * (title.count("\n") + 1) + 8,
             body, bsize, "#1e1e1e", w - 28),
    ]


def arrow(x1, y1, x2, y2, color="#1e1e1e", label=None, dashed=False,
          label_dx=0, label_dy=-18, label_w=160, label_size=11):
    e = base("arrow", x1, y1, x2 - x1, y2 - y1, stroke=color)
    e["points"] = [[0, 0], [x2 - x1, y2 - y1]]
    e.update({
        "lastCommittedPoint": None, "startBinding": None, "endBinding": None,
        "startArrowhead": None, "endArrowhead": "arrow",
        "strokeStyle": "dashed" if dashed else "solid",
    })
    out = [e]
    if label:
        out.append(text((x1 + x2) / 2 - label_w / 2 + label_dx,
                        (y1 + y2) / 2 + label_dy, label, label_size, color,
                        label_w, "center"))
    return out


def poly(x1, y1, pts, color="#1e1e1e", label=None, dashed=False,
         label_dx=0, label_dy=-18, label_w=120, label_size=11):
    e = base("arrow", x1, y1, pts[-1][0], pts[-1][1], stroke=color)
    e["points"] = [[0, 0]] + pts
    e.update({
        "lastCommittedPoint": None, "startBinding": None, "endBinding": None,
        "startArrowhead": None, "endArrowhead": "arrow",
        "strokeStyle": "dashed" if dashed else "solid",
    })
    out = [e]
    if label:
        mx = x1 + pts[0][0] + (pts[1][0] - pts[0][0]) / 2
        my = y1 + pts[0][1] + (pts[1][1] - pts[0][1]) / 2
        out.append(text(mx - label_w / 2 + label_dx, my + label_dy,
                        label, label_size, color, label_w, "center"))
    return out


def save(els, name):
    path = os.path.join(FIG, name)
    json.dump({
        "type": "excalidraw", "version": 2, "source": "bi-dss",
        "elements": els,
        "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None},
        "files": {},
    }, open(path, "w", encoding="utf8"), ensure_ascii=False)
    print("saved", path)


B, BS = "#a5d8ff", "#1971c2"      # fact / core
G, GS = "#b2f2bb", "#2f9e44"      # shared dimension / validated
P, PS = "#d0bfff", "#6741d9"      # fact-specific dimension
O, OS = "#ffd8a8", "#e8590c"      # source / movement
Y, YS = "#ffec99", "#f08c00"      # consumer
Gy, GyS = "#e9ecef", "#495057"    # storage

# ---------------------------------------------------------------- Figure 6.1
E = [text(40, 20, "Hình 6.1. Mô hình Star Schema cho phân tích chậm/hủy chuyến 2022–2023", 24)]
E.append(text(40, 58, "6 bảng chiều conformed • 2 bảng fact • 1.151.344 chuyến bay • 397.131 dòng nguyên nhân", 15, "#495057"))

E += card(80, 160, 300, 150, G, GS, "DimDate (730)",
          "DateKey • Year • Quarter\nMonth • Weekday\nIsWeekend • Season")
E += card(80, 420, 300, 150, G, GS, "DimCarrier (17)",
          "CarrierKey • CarrierCode\nCarrierName • DotCode\nAirlineDot")
E += card(80, 680, 300, 160, G, GS, "DimAirport (370)",
          "AirportKey • Code • Name\nCity • State • Region\nLatitude • Longitude")

E += card(650, 160, 420, 240, B, BS, "FactFlight (1.151.344)",
          "Grain: 1 dòng / chuyến\nDateKey • CarrierKey • OriginKey\nDestKey • RouteKey • StatusKey\nArrDelayMinutes • IsArrDelayed15\nIsCancelled • IsDiverted • Distance",
          18, 14)
E += card(650, 680, 420, 240, B, BS, "FactDelayReason (397.131)",
          "Grain: 1 dòng / chuyến × nguyên nhân\nDateKey • CarrierKey • OriginKey\nFlightKey • DelayReasonKey\nDelayMinutes (> 0)\n(nguồn: 5 cột DELAY_DUE_*)",
          18, 14)

E += card(1340, 160, 340, 160, P, PS, "DimRoute (6.820)",
          "RouteKey • Origin-Dest\nOriginAirportKey / DestAirportKey\nDistanceGroup")
E += card(1340, 420, 340, 160, P, PS, "DimFlightStatus (4)",
          "On-time • Delayed\nCancelled • Diverted\nIsCompleted • IsDisrupted")
E += card(1340, 680, 340, 160, P, PS, "DimDelayReason (5)",
          "Carrier • Weather • NAS\nSecurity • Late Aircraft\nTên & mô tả tiếng Việt")

E += arrow(380, 230, 650, 230, GS, "DateKey", label_dy=-20)
E += arrow(380, 290, 650, 760, GS)
E += arrow(380, 495, 650, 350, GS, "CarrierKey", label_dy=-20)
E += arrow(380, 520, 650, 820, GS, "CarrierKey", label_dy=40)
E += arrow(380, 760, 650, 395, GS, "AirportKey", label_dy=-20)
E += arrow(380, 790, 650, 880, GS, "AirportKey", label_dy=15)
E += arrow(1340, 240, 1070, 240, PS, "RouteKey", label_dy=-20)
E += arrow(1340, 500, 1070, 360, PS, "StatusKey", label_dy=-20)
E += arrow(1340, 760, 1070, 760, PS, "ReasonKey", label_dy=-20)
E += arrow(860, 680, 860, 400, OS, "FlightKey (drill-through)", dashed=True,
           label_dy=0, label_dx=95, label_w=190, label_size=11)

E.append(box(80, 960, 1600, 70, "#fff4e6", OS))
E.append(text(100, 975,
              "Nét liền: quan hệ active trong Power BI. Nét đứt: drill-through tùy chọn qua FlightKey.\n"
              "Ba bảng chiều dùng chung cho cả hai fact: DimDate, DimCarrier, DimAirport.",
              13, "#1e1e1e", 1560))
save(E, "fig_6_1_star_schema.excalidraw")

# ---------------------------------------------------------------- Figure 6.2
E = [text(40, 20, "Hình 6.3. Kiến trúc triển khai BI & DSS trên ai1-k3s", 24)]
E.append(text(40, 58, "GitOps bằng Flux CD • dbt-duckdb trong Job/CronJob • dữ liệu và export trên PVC local-path", 15, "#495057"))
E.append(box(260, 110, 1300, 720, "transparent", BS, dashed=True))
E.append(text(280, 122, "ai1-k3s · namespace bi-dss", 16, BS))

E += card(40, 140, 190, 130, O, OS, "GitHub", "bi-dss-platform\nmain (public)", 16, 13)
E += card(300, 180, 280, 120, B, BS, "Flux CD", "GitRepository + Kustomization\nreconcile mỗi 1 phút", 16, 13)
E += card(620, 180, 320, 120, B, BS, "Job / CronJob", "bi-dss-build-v2\nbi-dss-refresh (suspend)\nimage: bi-dss-toolbox:1.0.0", 16, 12)
E += card(980, 170, 340, 160, Gy, GyS, "PVC bi-dss-data (20Gi)",
          "/data/raw/flights_sample_3m.csv\n/data/warehouse/warehouse.duckdb\n/data/exports/powerbi/*.csv", 16, 12)

E += card(300, 400, 280, 120, O, OS, "Bootstrap (one-time)", "loader pod: archive.zip\n→ /data/raw (3.000.000 dòng)", 16, 13)
E += card(620, 390, 320, 140, G, GS, "dbt-duckdb", "staging → intermediate\n→ 6 dims + 2 facts\n→ CSV export", 16, 13)
E += card(980, 390, 340, 140, G, GS, "Data contracts", "8 contract enforced\n145 dbt tests\n185 runtime checks (0 fail)", 16, 13)
E += card(1600, 390, 260, 150, Y, YS, "Power BI", "8 CSV\npowerbi_relationships.csv\n_manifest.json", 16, 13)

E.append(box(300, 570, 1020, 110, "#ffffff", OS))
E.append(text(320, 586,
              "Luồng chạy: Flux apply → Job chờ raw data → dbt build → export CSV → Power BI.\n"
              "Kết quả: FactFlight 1.151.344 dòng; FactDelayReason 397.131 dòng; dbt PASS 145/145.",
              14, "#1e1e1e", 980))

E += arrow(230, 205, 300, 205, OS, "git poll", label_dy=-20, label_w=80)
E += arrow(580, 240, 620, 240, BS, "apply", label_dy=-20, label_w=60)
E += arrow(780, 300, 780, 390, BS, "run", label_dx=10, label_dy=-35, label_w=40)
E += poly(580, 460, [(0, -120), (400, -120), (400, -130)], OS, "raw CSV",
          label_dx=-130, label_dy=-20, label_w=90)
E += arrow(900, 390, 980, 330, GS, "read/write", label_dx=10, label_dy=20, label_w=80)
E += arrow(940, 470, 980, 470, GS, "tests", label_dx=-20, label_dy=-22, label_w=50)
E += arrow(1150, 390, 1150, 330, GS, "report", label_dx=45, label_dy=-15, label_w=60)
E += arrow(1320, 250, 1600, 450, BS, "CSV export", label_dy=-18, label_w=90)
save(E, "fig_6_3_k3s_architecture.excalidraw")

# ---------------------------------------------------------------- Figure 6.3
E = [text(40, 20, "Hình 6.2. Tách FactDelayReason bằng UNPIVOT 5 cột nguyên nhân", 24)]
E.append(text(40, 58, "Từ 1 dòng/chuyến sang 1 dòng/chuyến × nguyên nhân; chỉ giữ DelayMinutes > 0", 15, "#495057"))

E += card(80, 150, 480, 280, B, BS, "FactFlight (1.151.344)",
          "Grain: 1 dòng / chuyến\nMetrics: ArrDelayMinutes, DepDelayMinutes,\nIsArrDelayed15, IsCancelled, IsDiverted\n\n"
          "5 cột nguyên nhân (NULL nếu không trễ):\nDELAY_DUE_CARRIER • WEATHER • NAS\nSECURITY • LATE_AIRCRAFT",
          18, 14)
E += arrow(560, 290, 900, 290, OS, "UNPIVOT 5 cột\n+ DelayMinutes > 0", label_dy=-70, label_w=240, label_size=13)
E += card(900, 150, 520, 280, G, GS, "FactDelayReason (397.131)",
          "Grain: 1 dòng / chuyến × nguyên nhân\nFK: DateKey • CarrierKey • OriginKey\nFlightKey • DelayReasonKey\n"
          "Measure: DelayMinutes (> 0)\n\nChỉ giữ nguyên nhân có số phút > 0",
          18, 14)

E.append(box(80, 500, 550, 140, "#ffffff", BS))
E.append(text(100, 518, "245.127 chuyến chậm ≥ 15 phút\n397.131 dòng = 1,62 nguyên nhân/chuyến\n86 chuyến có 4 nguyên nhân", 14, "#1e1e1e", 510))
E.append(box(680, 500, 740, 140, "#ffffff", OS))
E.append(text(700, 518, "Tổng 16.815.593 phút\nLATE_AIRCRAFT 39,08% • CARRIER 37,81%\nNAS 17,46% • WEATHER 5,44% • SECURITY 0,21%", 14, "#1e1e1e", 700))
E.append(box(80, 680, 1340, 60, G, GS))
E.append(text(100, 696, "Đối soát: tổng 5 nguyên nhân = ARR_DELAY cho 245.127/245.127 chuyến (lệch 0 phút)", 15, "#1e1e1e", 1300))
save(E, "fig_6_2_fact_unpivot.excalidraw")
