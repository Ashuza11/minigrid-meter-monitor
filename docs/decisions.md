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
- **SPIKE baseline changed** (calibrated on 1,423,360 observed readings, 2024 H1): 75% of observed readings are 0,
  so the plain 7-day median was 0 for 83.5% of slots and "5 × 0" would flag any usage at all. The baseline is now
  the **median of the previous 7 days' non-zero readings** (typical level when the meter is in use), with a
  **minimum of 0.001 kWh per 15 min** (4 W; about the 25th percentile of non-zero readings) and at least
  **24 non-zero readings of history**. Consecutive spiking slots are merged into one event.
- **ZERO_RUN means "no consumption"**, not "faulty meter". The first idea was "customer out of credit", but the
  payments data does not support it: only 13 of 184 zero runs that ended with usage resuming had a payment within
  a week. Either top-ups also happen through channels missing from `paymentconfirmations` (it contains only mobile
  money: M-Pesa, Paga), or the household simply wasn't using power. Reported as an open question, not a conclusion.
- **Rules run on observed values only**; reconstructed values are excluded so the rules don't flag estimates.

## 5. Additions beyond the original spec

Added because the new dataset supports them and they are standard mini-grid KPIs:
- **kWh and revenue by customer class** (Residential / Commercial / Religious Institution).
- **ARPU** (average revenue per user per month) and **kWh per customer per month**.
- **Hourly load profile in local time** per site (Trends sheet).
- **Observed vs reconstructed share** per meter and site (data-quality report).
- ~~Energy sold per kWp of installed PV~~: **replaced by PV yield per kWp** (solar energy produced per kW of
  panels per day) from `vrmgeneration`. The sample holds 50 meters per site, not every customer, so
  "sample kWh / site kWp" would understate the real figure. See section 9.

Added after downloading `paymentconfirmations`, `sparkmetertransactions` and `vrmgeneration` (2026-11-20):
- **Billed vs collected**: estimated billed revenue (kWh × tariff) compared with payments actually received, per site and month.
  - Source: `paymentconfirmations.parquet`. Its `customerAccountNumber` is the same pseudonymised ID as `meter_customer_code` in the readings. In Jan–Jun 2024, every payer at Lomekwi (61) and Lorengelup (43), and 185 of 188 at Ekong Anaku, matched a meter in the readings.
  - Not used: `sparkmetertransactions.parquet`. It is the metering platform's internal ledger: in the window roughly half its rows are `pending` and half `processed`, which suggests one transaction can appear more than once. Using it would risk double counting.
- **ZERO_RUN context**: whether a zero-consumption run ends shortly after a payment (points to a credit problem rather than a fault).
- **Generated vs delivered vs metered** (done, section 9): `vrmgeneration.parquet` has rows for all three sites in the window. Its columns are per-device (PV inverters, battery monitor) and its timestamp is **local time** (`timestamp_local`), unlike the UTC meter readings.

## 6. Open items

- ~~Which `vrmgeneration` columns give PV energy~~: decided, section 9.
- Lorengelup meters only 39% of the energy its system delivers (section 9). The cause can't be determined
  from the data; it needs a site visit or the operator's network records.
- Payments cover mobile money only, so "billed vs collected" may understate collections (cash top-ups unknown).
- GAP also catches meter lifecycle: a meter first seen in April gets a GAP from 1 January. Kept, because the
  "while active" completeness figure already separates the two in the data-quality report.

## 7. First anomaly run (2024 H1, 150 meters)

- NEGATIVE and FLATLINE found **0** events. Checked directly: there are 0 negative observed values, and the
  longest identical non-zero run is 15 slots (3.75 h), below the 12 h threshold. Both rules stay: they guard
  against faults that this sample happens not to contain.
- Ekong Anaku has most GAP events because ~70% of its data is reconstructed, leaving short islands of real
  readings between gaps; its SPIKE and ZERO_RUN counts are therefore not comparable with the Kenyan sites.
- Exchange-rate source and values: see section 8.

## 8. Aggregation (2024 H1)

### Exchange rates (the only external input)

`reference/usd_exchange_rates_2024h1.csv` holds one rate per currency and month, with its source:

| Month | KES per USD | NGN per USD |
|---|---|---|
| 2024-01 | 159.69 | 941.26 |
| 2024-02 | 151.84 | 1,506.84 |
| 2024-03 | 137.35 | 1,510.84 |
| 2024-04 | 131.57 | 1,236.56 |
| 2024-05 | 131.69 | 1,435.57 |
| 2024-06 | 129.36 | 1,481.77 |

- **KES**: Central Bank of Kenya, *Monthly Exchange rate (period average)*, column "United States dollar"
  (https://www.centralbank.go.ke/statistics/exchange-rates/). Cross-check: the CBK Quarterly Economic Reviews
  give averages of 149.63 for Q1 and 130.87 for Q2 2024, which equal the means of these monthly values.
- **NGN**: Central Bank of Nigeria publishes daily rates, not monthly averages
  (https://www.cbn.gov.ng/rates/ExchRateByCurrency.html). The value is the mean of the daily US DOLLAR
  `centralrate` for each month (17–22 business days). 2024-02-22 has two entries, which were averaged.
- Why monthly averages: revenue accrues across the month, and the naira lost ~40% in H1 2024 (official
  rate ~900 in early January, ~1,500 by June). One rate for the whole period would distort the USD trend.
- Retrieved 2026-11-20.

### Pricing choices

| Choice | Why |
|---|---|
| Price = `totalRate` (VAT included); net = `totalRate / (1 + vatRate)` | For Ekong Anaku, `baseRate x (1 + vatRate)` = 299.93 does not equal `totalRate` = 399.79. The data supports `totalRate` (next row), so the net amount is derived from it rather than read from `baseRate`. |
| Billed revenue = daily kWh (observed + reconstructed) x the tariff in force that day (`merge_asof`) | Over H1, payments collected equal **99.6%** (Ekong Anaku), **102.9%** (Lomekwi) and **96.2%** (Lorengelup) of this billed estimate. With observed kWh only, Ekong Anaku billing would cover ~61% of what customers paid, so reconstructed energy is real, sold energy. |
| Partial local days dropped | Period reported: 2 Jan – 30 Jun 2024 (181 full local days per meter). Payments are filtered to the same local days. |
| "Customer with use" = meter with kWh > 0 in the month | Denominator for ARPU and kWh per customer, so meters that were idle all month don't dilute the averages. |

### Headline results (sample of 50 meters per site)

| Site | kWh (H1) | Observed share of kWh | Billed | Collected | Collected / billed |
|---|---|---|---|---|---|
| Ekong Anaku | 1,472.9 | 61.1% | NGN 588,851 (USD 449) | NGN 586,762 (USD 446) | 99.6% |
| Lomekwi | 1,625.6 | 87.6% | KES 96,173 (USD 692) | KES 99,003 (USD 712) | 102.9% |
| Lorengelup | 672.7 | 99.0% | KES 33,554 (USD 238) | KES 32,268 (USD 230) | 96.2% |

- Monthly ARPU: Lomekwi USD 2.15–3.26, Ekong Anaku USD 0.94–2.18, Lorengelup USD 0.77–1.01.
- Ekong Anaku's USD revenue halves from January to February while its naira revenue falls only 16%: most of
  the USD drop is the currency, not the customers. This is why both currencies are shown.
- Peak local hour: 19:00 at Lomekwi, 20:00 at Ekong Anaku, but **10:00 at Lorengelup**, where commercial
  customers (9 of 50) use more energy than the 40 residential ones in every month.
- Observed share of kWh (61% at Ekong Anaku) is much higher than its slot completeness (median 8%): the meters
  that do report are the larger consumers.

## 9. Energy balance (generation step)

### What the monitoring data is

`vrmgeneration.parquet` is the Victron VRM remote monitoring of each site's power system. Its rows are
**instantaneous power in watts** (not energy), recorded at irregular times: mostly every 15 minutes, with bursts
of rows seconds apart. Timestamps are local time.

| Quantity | Columns used | Note |
|---|---|---|
| Solar produced (`pv_kwh`) | sum of every `Solar_Charger_*_PV_power` | DC-coupled chargers. Our sites have no `PV_Inverter_*` data. |
| Delivered to the village (`load_kwh`) | sum of `System_overview_AC_Consumption_L1..L3` | AC output of the inverters, before the village network. |
| Diesel generator (`genset_kwh`) | sum of `System_overview_Genset_L1..L3` | Recorded only while running, so empty = 0 W. |
| Battery charge (`battery_soc_pct`) | `System_overview_Battery_SOC` | Context for solar yield. |

### Method

| Choice | Why |
|---|---|
| Power to energy: mean watts in each 15-minute slot x 0.25 h / 1000 | Matches the meter interval. Averaging handles bursts (many rows in one slot) without over-weighting them. |
| Meter side = **all** meters at the site, summed per slot in `ingest.py` | Our 50-meter sample is 77% of Lomekwi's meters, 81% of Lorengelup's and about 26% of Ekong Anaku's (up to 195 meters), so it can't be compared with whole-site generation. |
| Compare only slots where solar AND delivered power were monitored; meter totals summed over the same slots | Like-for-like totals: a monitoring gap doesn't make the meters look larger than the delivered energy. |
| A day is included if at least 90% of its slots are monitored (`GENERATION_MIN_COVERAGE`) | Included days: Lomekwi 157 / 181, Lorengelup 154 / 181, Ekong Anaku 109 / 181. |
| PV yield per kWp per day = pv_kwh / pv_kwp / monitored days | "Monitored days" = monitored slots / 96, so partly monitored days count in proportion. |

**Time-alignment check.** Correlation between delivered power and meter totals is highest with no time shift
at all three sites (shifts of -2 h to +2 h tested). That confirms that meter readings are UTC, the monitoring is
local time, and the conversion is correct.

### Results (2 Jan – 30 Jun 2024, included days)

| Site | Solar produced | Delivered | Metered (all meters) | Metered / delivered | PV yield (kWh/kWp/day) | Avg battery charge |
|---|---|---|---|---|---|---|
| Ekong Anaku | 4,239 kWh | 3,519 kWh | 2,963 kWh | **84.2%** | 3.19 | 46% |
| Lomekwi | 2,958 kWh | 2,190 kWh | 1,640 kWh | **74.9%** | 0.84 | 92% |
| Lorengelup | 2,368 kWh | 1,627 kWh | 640 kWh | **39.3%** | 1.60 | 90% |

- **Delivered vs produced** (69–83%) covers battery charge and discharge losses, inverter losses, and the
  system's own consumption.
- **Metered vs delivered** is the distribution figure: line losses, unmetered loads, theft and meter faults.
  Ekong Anaku and Lomekwi are in a plausible range for small networks. **Lorengelup is an outlier.** Its meter
  data is 99% observed, so this is not a data gap. The unmetered power is present at every hour (about 150 W
  during the day and 330–400 W at night), which points to a constant or night-time unmetered load, such as
  street or security lighting, rather than to customer behaviour. This is a hypothesis to verify on site.
- **Low PV yield in Kenya is a demand issue, not a solar issue.** Lomekwi produces 0.84 kWh per kWp per day
  with its battery at 92% on average: the battery is usually full and demand is low, so the chargers limit
  solar input. Ekong Anaku, with higher demand, reaches 3.19 with the battery at 46%. Lomekwi has spare
  capacity for new customers or productive-use loads.
- Ekong Anaku used its diesel generator in March only (97 kWh), the one month where solar fell short of demand.
- At Ekong Anaku, meter totals are above delivered power at night. Its meter data is only 60% observed, and the
  reconstructed values are estimates, so its hourly shape is approximate. Monthly totals are
  still consistent (84%).
