# Decisions and changes from the original spec

The project started as a London smart-meter pipeline with simulated sites and a flat
tariff. After switching to real mini-grid data, several requirements changed. Each
change is listed with the reason, so every difference from the original brief is
traceable. Facts quoted here come from `explore.py` runs, not assumptions.

## 1. Data source

| Original spec | Now | Why |
|---|---|---|
| Kaggle "Smart meters in London" (UK households, 2011–2014) | **Renewvia mini-grid dataset** (Kenya and Nigeria), plus a **DR Congo microgrid case study** (KIT dataset) | Real mini-grid customers are far closer to the target job than UK households. |
| Sites A/B/C randomly assigned (simulated) | **Real sites**: Lomekwi (Kenya), Lorengelup (Kenya), Ekong Anaku (Nigeria) | The dataset already groups meters by real mini-grid, so nothing needs simulating. |
| ~150 meters, 3–6 months | **50 meters per site (150 total), sampled with seed 42**, window **2024-01-01 to 2024-06-30** | From 2024-01 to 2024-06 tariffs are stable and both Kenyan sites are mostly observed (≥ 88% in 5 of 6 months, with real gaps to detect). Tariffs before 2023-05 are recorded as 0, so earlier windows can't support revenue figures. |
| Licence: not discussed | Renewvia: **CC BY-SA 4.0**; KIT: **CC BY 4.0** | Processed outputs derived from Renewvia are shared under CC BY-SA 4.0 with attribution. |

## 2. Data shape

| Original spec | Now | Why |
|---|---|---|
| Half-hourly readings | **15-minute readings** | That is the dataset's resolution. The interval is a config value; rules are defined in hours/days and converted to slots (e.g. 6 h = 24 slots). |
| Missing = no row | **Missing = no row OR a reconstructed value** (`imputation_method` is `profile` or `uniform`) | The "clean" files fill communication gaps with estimates. Counting estimates as real data would report Ekong Anaku as complete when only 17% of its rows are observed. |
| Timezone: assumed local, DST edge cases | **Timestamps are UTC**; local time = UTC + site offset (Kenya +3, Nigeria +1); neither country uses DST | Evidence: the evening peak appears at 16:00 stored time in Kenya, i.e. 19:00 local. DST tests are kept so the code stays correct for other regions. |

### Data quality: two completeness figures (added after the first clean run)

The brief defines completeness as observed slots / all slots in the window. The first run showed that missing
data comes in long per-meter stretches (median longest gap 8 days at Lomekwi, up to 139 days), often because a
meter starts reporting after the window begins or stops before it ends. So the report has both:

- `completeness_pct`: observed slots / all slots in the window (the brief's definition, used for the 90% target).
- `completeness_active_pct`: observed slots / slots between the meter's first and last reading. This separates
  meter lifecycle (connected late, disconnected early) from unreliable reporting while in service.

### Local time and the window edges

Readings are selected by a UTC window, but days and months are reported in local time (what customers and
the company use). So each site's local series starts a few hours after local midnight on 1 January and runs a
few hours into 1 July (Kenya: 3 h, Nigeria: 1 h). Aggregation drops those partial local days at the edges.

## 3. Tariff and accounting

| Original spec | Now | Why |
|---|---|---|
| One flat illustrative tariff in USD/kWh | **Real tariffs per site and customer class**, time-varying, in local currency (KES, NGN), from `tariffs.parquet` | Real prices make the revenue table meaningful. |
| `estimated_revenue_usd` | Revenue in **local currency** (VAT-inclusive `totalRate` and net-of-VAT `baseRate`) **and in USD** | The dataset has no usable exchange rate (`forexRate` is 0). USD uses official monthly average rates (central banks), cited in config. This is the only external assumption. |

## 4. Anomaly rules

Thresholds stay as specified. Severity cut-offs approved on 2026-11-20:

| Rule | Threshold | Low | Medium | High |
|---|---|---|---|---|
| GAP | ≥ 6 h without observed data | 6–24 h | 24–72 h | > 72 h |
| ZERO_RUN | ≥ 3 days of zero consumption | — | 3–6 days | ≥ 7 days |
| SPIKE | reading > 5 × the previous 7 days' median | 5–10× | 10–20× | > 20× |
| NEGATIVE | value < 0 | — | — | always |
| FLATLINE | same non-zero value ≥ 12 h | 12–24 h | 24–48 h | > 48 h |

Boundary convention: **upper limits are inclusive** (exactly 24 h is still "low", exactly 72 h is "medium"), so ">72 h" and ">20×" read exactly as in the table. One consistency consequence: ZERO_RUN is "medium" up to and including 7.0 days and "high" above 7 days (the table originally said "7+ days").

Adjustments driven by the data:
- **SPIKE minimum baseline** will be calibrated on real data, because most 15-min readings are 0 (median 0 kWh), so a raw median baseline would flag every non-zero reading.
- **ZERO_RUN is interpreted as "no consumption"**, not "faulty meter": on a prepaid mini-grid it most often means the customer has no credit.
- **Rules run on observed values only**; reconstructed values are excluded so the rules don't flag estimates.

## 5. Additions beyond the original spec

Added because the new dataset supports them and they are standard mini-grid KPIs:
- **kWh and revenue by customer class** (Residential / Commercial / Religious Institution).
- **ARPU** (average revenue per user per month) and **kWh per customer per month**.
- **Hourly load profile in local time** per site (Trends sheet).
- **Observed vs reconstructed share** per meter and site (data-quality report).
- **Energy sold per kWp of installed PV** per site and month, from `minigridprojects.parquet` (`sizePv`).

Added after downloading `paymentconfirmations`, `sparkmetertransactions` and `vrmgeneration` (2026-11-20):
- **Billed vs collected**: estimated billed revenue (kWh × tariff) compared with payments actually received, per site and month.
  - Source: `paymentconfirmations.parquet`. Its `customerAccountNumber` is the same pseudonymised ID as `meter_customer_code` in the readings. In Jan–Jun 2024, every payer at Lomekwi (61) and Lorengelup (43), and 185 of 188 at Ekong Anaku, matched a meter in the readings.
  - Not used: `sparkmetertransactions.parquet`. It is the metering platform's internal ledger: in the window roughly half its rows are `pending` and half `processed`, which suggests one transaction can appear more than once. Using it would risk double counting.
- **ZERO_RUN context**: whether a zero-consumption run ends shortly after a payment (points to a credit problem rather than a fault).
- **Generated vs sold** (to be explored): `vrmgeneration.parquet` has rows for all three sites in the window. Its columns are per-device (PV inverters, battery monitor) and its timestamp is **local time** (`timestamp_local`), unlike the UTC meter readings.

## 6. Open items

- Which `vrmgeneration` columns give PV energy for each site: decided when that step starts.
- Exact SPIKE minimum baseline: set in the anomalies step from real data.
- Exchange-rate source and values: added in the aggregation step.
