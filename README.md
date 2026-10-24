# BI & DSS — Flight Delay Star Schema on ai1-k3s

End-to-end data platform for the US domestic flight dataset (`flights_sample_3m.csv`, 2019–2023), modelling **2022–2023** into a Kimball star schema with dbt, DuckDB, Kubernetes and Flux CD.

## Bàn giao cho Phúc — CSV, Python, Word và DAX

Bộ bàn giao hiện hành: [`SkyOps_Technical/README.md`](SkyOps_Technical/README.md).
Tám bảng nạp Power BI nằm trong `SkyOps_Technical/data/processed/`; **FactDelay.csv** là tên xuất của bảng SQL `marts.fact_delay_reason`. Các cột xuất dùng PascalCase; SQL/contracts vẫn dùng snake_case.

`FactFlight.csv` vượt giới hạn 100 MB của GitHub nên lưu bằng **Git LFS**. Clone bằng `git clone` rồi `git lfs pull`; không nạp tệp pointer vào Power BI. Bản ZIP đính kèm GitHub Release chứa CSV thật, không yêu cầu LFS.

Đường chạy Pandas không cần Kubernetes: chạy `02_build_dimensions.py` rồi `03_build_facts.py` theo README bàn giao. Hai đường ETL dùng cùng quy tắc/khóa; `pipelines/validate_handoff.py --database warehouse.duckdb` đối soát toàn bộ giá trị CSV với dbt.

Hợp đồng mô hình: `DimAirport → FactFlight[OriginAirportKey]` Active; vai trò `DestAirportKey` **Inactive**; mọi quan hệ đều **Single từ Dim sang Fact**. Không tạo quan hệ active giữa hai fact hoặc từ DimAirport sang DimRoute. Tham khảo `powerbi_relationships.csv`, không dùng tự động phát hiện relationship.

Word bàn giao và DAX là tài liệu/mã nguồn cho các mục 6.2–6.3, 6.4 và 6.6; chưa phải `.pbix` hay ảnh chụp Model View. Kiểm chứng CSV không đồng nghĩa đã chạy DAX trong Power BI.

## Architecture

```text
flights_sample_3m.csv (PVC)
        │
        ▼
dbt-duckdb: staging → intermediate → marts
        │                         │
        │                         ├── 6 dimensions
        │                         └── 2 fact tables
        ▼
CSV exports + manifest + contract report (PVC)
        │
        ▼
Power BI
```

- **Runtime:** namespace `bi-dss` on `ai1-k3s`, `local-path` PVC `bi-dss-data` (20 GiB).
- **Image:** `bi-dss-toolbox:1.0.0` (Python 3.12, dbt-core 1.12.6, dbt-duckdb 1.11.0, DuckDB 1.5.6). Built locally and imported into k3s containerd.
- **GitOps:** Flux `GitRepository/bi-dss-repo` + `Kustomization/bi-dss` in `flux-system`.
- **Jobs:** `Job/bi-dss-build-v2` for the initial build; `CronJob/bi-dss-refresh` (suspended) for scheduled refreshes.
- **No secrets in Git.** The raw CSV is intentionally not committed.

## Data model

| Table | Grain | Rows |
| --- | --- | ---: |
| `marts.fact_flight` | one row per flight | 1,151,344 |
| `marts.fact_delay_reason` | one row per flight × delay reason (> 0 minutes) | 397,131 |
| `marts.dim_date` | one row per date, 2022-01-01…2023-12-31 | 730 |
| `marts.dim_carrier` | one row per `carrier_code` | 17 |
| `marts.dim_airport` | one row per `airport_code` | 370 |
| `marts.dim_route` | one row per origin–destination | 6,820 |
| `marts.dim_delay_reason` | one row per BTS delay cause | 5 |
| `marts.dim_flight_status` | On-time / Delayed / Cancelled / Diverted | 4 |

`FactFlight` keeps the flight-level measures (`ArrDelayMinutes`, `DepDelayMinutes`, `IsArrDelayed15`, `IsCancelled`, `IsDiverted`, taxi/air/elapsed/distance, `Excess_Delay_Minutes`). `FactDelay` is the unpivoted delay-cause export, sharing `DateKey`, `CarrierKey`, `OriginAirportKey` and a lineage `FlightKey`; no active fact-to-fact relationship is required.

## Repository layout

```text
analytics/                 dbt project (models, contracts, tests, seed)
pipelines/                 raw profiling, airport enrichment, export, contracts
contracts/                 machine-readable data contract registry
deploy/k8s/                namespace, PVC, quota, network policy, Job, CronJob
deploy/flux/               Flux GitRepository + Kustomization
deploy/bootstrap/          one-time raw-data loader pod
docker/Dockerfile          pinned toolbox image
scripts/                   image import, data bootstrap, download, verify
```

## Deploy on ai1-k3s

Prerequisites: `kubectl` with `~/.kube/config`, Docker, passwordless `sudo k3s`, Flux installed.

```bash
# 1. Build and import the toolbox image into k3s containerd
bash scripts/import_image.sh

# 2. Register the Flux source and Kustomization
export KUBECONFIG=$HOME/.kube/config
kubectl apply -f deploy/flux/source.yaml
kubectl apply -f deploy/flux/bi-dss-kustomization.yaml

# 3. Load the raw CSV into the PVC (the Job waits for it)
bash scripts/bootstrap_data.sh /path/to/archive.zip

# 4. Watch the initial build
kubectl -n bi-dss get job,pod,pvc
kubectl -n bi-dss logs job/bi-dss-build-v2 -f

# 5. Download CSV exports and dbt artifacts
bash scripts/download_outputs.sh exports-k8s
```

Local (non-Kubernetes) run is also supported:

```bash
docker run --rm --user 1000:1000 -e HOME=/tmp \
  -v "$PWD":/workspace -w /workspace/repo \
  -e BI_DUCKDB_PATH=/workspace/warehouse/warehouse.duckdb \
  -e BI_RAW_CSV=/workspace/data/flights_sample_3m.csv \
  bi-dss-toolbox:1.0.0 \
  bash -lc 'cd analytics && dbt build --profiles-dir profiles && cd .. && python pipelines/export_powerbi.py && python pipelines/validate_contracts.py'
```

## Verification

- Local `dbt build`: **PASS=146, ERROR=0** (11 models, 1 seed, 134 tests).
- Data contracts: **186/186 checks PASS**; results are written to `meta.contract_validation`.
- Reconciliation: for all **245,127** delayed flights (`ARR_DELAY >= 15`), the sum of the five delay causes equals `ARR_DELAY` exactly (max diff = 0 minutes).
- Exports: 8 CSVs + `_manifest.json` + `powerbi_relationships.csv` + `contract_report.json`.
- Power BI loads the 2022–2023 facts only: 1,151,344 + 397,131 rows.

## Data quality notes

- 2 completed flights have `ARR_DELAY` missing while neither cancelled nor diverted; they remain `On-time` with null delay minutes.
- `2400` in scheduled departure HHMM is normalized to `0000`; hour extraction uses floor division (e.g. `1359 → 13`, not rounded `14`). Actual-time normalization is not claimed by this export.
- Airport enrichment uses OurAirports (public domain) plus manual overrides for historical codes. Territory state codes are retained; mapping PR/VI to South and GU/AS/MP to West is a project grouping, not an official Census classification of territories. Historical ISN metadata requires the provenance caveat in the handoff README.
- 17 carriers and 370 airports occur in 2022–2023 (full dataset: 18 and 380).
- 80 routes have two distance values; `DimRoute.distance_miles` uses the average.
- `FactDelay` deliberately omits `DestAirportKey`; it supports Date/Carrier/Origin/Reason analysis. Destination/Route/Status filters do not filter this fact in the conformed-dimension model. A dedicated DAX virtual relationship is needed if that analysis is later required.

## Documentation

The current handoff Word report is generated by `SkyOps_Technical/outputs/build_handoff_doc.py` using the handoff documentation and observed CSV validation evidence. See the handoff README for reproducible commands.

Earlier `docs/build_docx_6.py`, D2/Excalidraw sources and figures describe the prior dbt-only report, with historical names/counts. They are retained unchanged as historical project assets, not the current CSV schema, relationship contract or verification evidence.

## Operations

- The CronJob is suspended by default. Enable it with:
  `kubectl -n bi-dss patch cronjob bi-dss-refresh --type merge -p '{"spec":{"suspend":false}}'`
- When models change, bump the Job name (`bi-dss-build-v3`) or delete the completed Job; the CronJob always pulls the latest `main`.
- The toolbox image is local; after changing dependencies, rebuild and re-import with `scripts/import_image.sh`.
- Raw data lives only on the PVC. To refresh, replace `/data/raw/flights_sample_3m.csv` via the loader pod, then rerun the Job/CronJob.
