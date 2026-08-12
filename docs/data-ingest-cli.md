# Case Capital Data Ingestion CLI

`backend/data_ingest_cli.py` is a non-execution command surface for pulling free/provider-backed data into `raw_data_snapshots`.

It does not place orders, resize positions, or alter PM/trading state.

## Commands

Run from `backend`:

```powershell
.\.venv\Scripts\python.exe data_ingest_cli.py providers
.\.venv\Scripts\python.exe data_ingest_cli.py forex-factory --impact high --no-persist
.\.venv\Scripts\python.exe data_ingest_cli.py fred-series FEDFUNDS DGS10 --no-persist
.\.venv\Scripts\python.exe data_ingest_cli.py macro-calendar --days 45 --no-persist
.\.venv\Scripts\python.exe data_ingest_cli.py sec-ticker LDOS --no-persist
.\.venv\Scripts\python.exe data_ingest_cli.py ticker LDOS APLD --no-persist
.\.venv\Scripts\python.exe data_ingest_cli.py usaspending-probe --no-persist
.\.venv\Scripts\python.exe data_ingest_cli.py all -t LDOS -t APLD --no-persist
```

Use `--persist` or omit `--no-persist` to write raw snapshots into Mongo.
Use `--raw-output` when you need the full response printed to the console; otherwise the CLI prints a compact operational summary.

## Snapshot Contract

Persisted rows include:

- `source_key`
- `provider`
- `dataset`
- `source_timestamp`
- `received_timestamp`
- `knowledge_timestamp`
- `revision_timestamp`
- `dataset_version`
- raw `payload`

## Current Free Sources

- Alpaca: already handled by terminal adapters for broker truth and market data.
- ForexFactory/FairEconomy XML: global economic calendar.
- FRED: macro series and calendar releases.
- SEC EDGAR: ticker lookup and companyfacts.
- ClinicalTrials.gov/openFDA: handled through existing terminal adapters.
- USAspending.gov: public agency/reference probe and existing contracts adapters.
- London Strategic Edge: used when configured.
