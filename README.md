# MiniGrid Meter Monitor

A smart-meter data pipeline and dashboard for community solar mini-grids:
ingest → clean → rule-based anomaly detection → aggregation and revenue accounting → AWS S3 → QuickSight / Power BI.

> Work in progress: this README will be completed with real results from pipeline runs.

## Setup

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync                 # creates .venv with Python 3.11 and installs locked dependencies
cp .env.example .env    # then fill in your bucket and AWS profile
```

## Data sources

- **Renewvia mini-grid dataset** (core pipeline): Selby, N. (2026). *High-Frequency, Multi-Domain Operational Dataset for Community Solar Mini-Grids in Kenya and Nigeria* (Version 1.0) [Dataset]. Zenodo, https://zenodo.org/records/22149102. Licensed CC BY-SA 4.0; processed outputs derived from it are shared under the same licence.
- **KIT microgrid dataset** (DR Congo case study): Luh et al. (2022). *High-resolution real-world electricity data from three microgrids in the global south*. KIT RADAR, https://doi.org/10.35097/1305. Licensed CC BY 4.0.

Raw data is not included in this repository; see the download instructions (coming in `docs/`).
