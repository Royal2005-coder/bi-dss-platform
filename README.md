# BI & DSS — Flight Delay Star Schema on ai1-k3s

End-to-end data platform for the US domestic flight dataset (`flights_sample_3m.csv`, 2019–2023), modelling **2022–2023** into a Kimball star schema with dbt, DuckDB, Kubernetes and Flux CD.

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
- **Jobs:** `Job/bi-dss-build-v1` for the initial build; `CronJob/bi-dss-refresh` (suspended) for scheduled refreshes.
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

`FactFlight` keeps the flight-level measures (`ArrDelayMinutes`, `DepDelayMinutes`, `IsArrDelayed15`, `IsCancelled`, `IsDiverted`, taxi/air/elapsed/distance). `FactDelayReason` is the unpivoted delay-cause fact, sharing `DateKey`, `CarrierKey`, `OriginAirportKey` and `FlightKey`; destination can be obtained through `FactFlight` for drill-through.

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
kubectl -n bi-dss logs job/bi-dss-build-v1 -f

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

- `dbt build`: **PASS=145, ERROR=0** (11 models, 1 seed, 133 tests).
- Data contracts: **185/185 checks PASS**; results are written to `meta.contract_validation`.
- Reconciliation: for all **245,127** delayed flights (`ARR_DELAY >= 15`), the sum of the five delay causes equals `ARR_DELAY` exactly (max diff = 0 minutes).
- Exports: 8 CSVs + `_manifest.json` + `powerbi_relationships.csv` + `contract_report.json`.
- Power BI loads the 2022–2023 facts only: 1,151,344 + 397,131 rows.

## Data quality notes

- 2 completed flights have `ARR_DELAY` missing while neither cancelled nor diverted; they remain `On-time` with null delay minutes.
- `2400` in actual time columns is normalized to `0000` for `ScheduledDepTime`/hour/minute.
- Airport enrichment uses OurAirports (public domain) + US Census regions; PBI and ISN are manual overrides because OurAirports 2026 no longer carries the historical codes. PR/VI/GU/AS/MP are mapped to their territory codes.
- 17 carriers and 370 airports occur in 2022–2023 (full dataset: 18 and 380).
- 80 routes have two distance values; `DimRoute.distance_miles` uses the average.
- `FactDelayReason` deliberately omits `DestAirportKey` to keep the required shared dimension set; use `FlightKey` for destination drill-through.

## Operations

- The CronJob is suspended by default. Enable it with:
  `kubectl -n bi-dss patch cronjob bi-dss-refresh --type merge -p '{"spec":{"suspend":false}}'`
- When models change, bump the Job name (`bi-dss-build-v2`) or delete the completed Job; the CronJob always pulls the latest `main`.
- The toolbox image is local; after changing dependencies, rebuild and re-import with `scripts/import_image.sh`.
- Raw data lives only on the PVC. To refresh, replace `/data/raw/flights_sample_3m.csv` via the loader pod, then rerun the Job/CronJob.
