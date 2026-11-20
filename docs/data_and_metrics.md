# Data, Metrics and Decisions: A Non-Technical Guide

This document explains what the MiniGrid Meter Monitor measures, what each metric means, and why the main
design decisions were made. It is written for readers without a data-engineering background: operations
managers, finance staff, and investors. Technical details are in `docs/decisions.md`.

All figures come from running the pipeline on the data described below.

---

## 1. Context

A **mini-grid** is a local electricity network, typically solar panels with battery storage, that supplies a
village or town not connected to the national grid. Customers are **prepaid**. They buy credit with mobile
money and their smart meter supplies electricity while the credit lasts.

Each customer's **smart meter** records how much electricity passes through it. The unit is the
**kilowatt-hour (kWh)**: the energy used by a 100-watt appliance running for 10 hours.

## 2. The data

| Item | Value |
|---|---|
| Source | Renewvia Energy mini-grid dataset (Selby, 2026, Zenodo record 22149102, CC BY-SA 4.0) |
| Sites | Lomekwi and Lorengelup (Kenya), Ekong Anaku (Nigeria) |
| Customers | 50 per site, selected at random with a fixed seed so the selection is reproducible (150 in total) |
| Period | 1 January – 30 June 2024 |
| Resolution | One reading per meter every 15 minutes (96 readings per day) |
| Supporting tables | Site details (PV and battery size), tariffs, mobile-money payments |

**Consumption profile.** Customers use small amounts of energy, mostly for lighting, phone charging and small
appliances. About three quarters of all 15-minute readings are zero. Demand peaks in the evening at the
residential sites, at 19:00 local time in Lomekwi and 20:00 in Ekong Anaku.

## 3. Pipeline overview

| Stage | Purpose | Output |
|---|---|---|
| Ingest | Extract the selected sites, customers and period from the full dataset | Raw subset |
| Clean | Validate the data and build a complete 15-minute timeline per meter, so missing periods become visible | Clean readings, data-quality report |
| Anomalies | Apply five transparent rules that flag operational issues | Anomaly register |
| Aggregate | Total energy and revenue per day, month, site and customer class, in local currency and USD | Daily, monthly and accounting tables |
| Dashboard | Present results in four views: Overview, Trends, Anomalies, Accounting | QuickSight or Power BI |

## 4. Data quality

### 4.1 Observed and reconstructed readings

When a meter cannot transmit, for example during a mobile-network outage, the dataset's authors filled the
missing interval with an **estimate** (a *reconstructed* value). The pipeline labels every 15-minute interval:

- **Observed**: the meter reported the value.
- **Reconstructed**: the value was estimated by the dataset's authors.
- **No data**: no value exists.

The two kinds of value are used for different purposes.

| Purpose | Values used | Reason |
|---|---|---|
| Energy and revenue totals | Observed + reconstructed | The energy was delivered and paid for, even if the meter reported it late or not at all. Section 6 confirms this. |
| Data quality and anomaly detection | Observed only | An estimate cannot prove that a fault did or did not happen. |

### 4.2 Completeness

**Completeness** is the share of 15-minute intervals that hold an observed reading. The target is **90%**.

**Completeness while active** counts only the period between a meter's first and last reading. It separates
customers who joined or left during the period from meters that report unreliably while in service.

| Site | Median completeness | Median while active | Meters below 90% |
|---|---|---|---|
| Lorengelup | 94.1% | 94.7% | 21 of 50 |
| Lomekwi | 80.0% | 82.8% | 50 of 50 (mainly a January outage) |
| Ekong Anaku | 8.0% | 8.3% | 50 of 50 |

**Interpretation.** Missing data is not tied to a time of day and never affects a whole site at once. It occurs
in long stretches per meter, typically days or weeks. At Ekong Anaku most values are reconstructed, so its
results carry more uncertainty than those of the Kenyan sites.

## 5. Anomaly detection

Each rule has a **threshold**, which decides whether an event is flagged, and **severity levels**, which rank
how urgent it is. All rules use observed readings only. Every threshold is a stated, explainable business
rule; no machine-learning model is used.

| Rule | Definition | Threshold | Severity | Operational meaning |
|---|---|---|---|---|
| **GAP** | No observed reading | 6 hours or more | Low ≤ 24 h, Medium ≤ 72 h, High > 72 h | The operator cannot see the meter: a communication, meter or power fault |
| **ZERO_RUN** | Observed consumption of exactly zero | 3 days or more | Medium ≤ 7 days, High > 7 days | Lost revenue: the customer is absent, has no credit, or supply is interrupted |
| **SPIKE** | A reading far above the customer's normal level | More than 5 × the customer's typical in-use reading over the previous 7 days | Low ≤ 10 ×, Medium ≤ 20 ×, High > 20 × | A new large appliance, a fault, or possible tampering; risk of overloading the system |
| **NEGATIVE** | A reading below zero | Any | Always High | A meter or data error: consumption cannot be negative |
| **FLATLINE** | The same non-zero value repeated | 12 hours or more | Low ≤ 24 h, Medium ≤ 48 h, High > 48 h | A stuck meter: real consumption varies |

### Results (150 meters, January–June 2024)

| Rule | Low | Medium | High | Total | Meters affected |
|---|---|---|---|---|---|
| GAP | 7,809 | 1,179 | 330 | 9,318 | 150 |
| SPIKE | 1,151 | 457 | 549 | 2,157 | 77 |
| ZERO_RUN | – | 211 | 135 | 346 | 56 |
| NEGATIVE | – | – | 0 | 0 | 0 |
| FLATLINE | 0 | 0 | 0 | 0 | 0 |

- NEGATIVE and FLATLINE found no events. This was checked directly in the data. The rules are kept to guard
  against faults that this sample does not contain.
- Most GAP events at Ekong Anaku reflect its low completeness, so its anomaly counts should not be compared
  directly with the Kenyan sites.

## 6. Financial metrics

| Metric | Definition |
|---|---|
| **Tariff** | Price per kWh charged to the customer, VAT included: KES 59.16 (Lomekwi), KES 49.88 (Lorengelup), NGN 399.79 (Ekong Anaku). Unchanged throughout the period. |
| **Billed revenue** | Energy used × tariff in force that day. An estimate of what customers were charged. |
| **Revenue excluding VAT** | Billed revenue ÷ (1 + VAT rate). VAT is 16% in Kenya and 7.5% in Nigeria. |
| **Collected revenue** | Mobile-money payments actually received (M-Pesa, Paga). |
| **Collected / billed** | Collected ÷ billed. Close to 100% means the tariff and energy figures are consistent with real cash flow. |
| **Customer with use** | A meter that consumed energy during the month. |
| **ARPU** | Average Revenue Per User: billed revenue ÷ customers with use, per month. |
| **kWh per customer** | Energy ÷ customers with use, per month. |
| **USD values** | Local currency ÷ the official average exchange rate for that month. |

### Results (50 sampled customers per site, 2 January – 30 June 2024)

| Site | Energy (kWh) | Billed | Collected | Collected / billed | Monthly ARPU (USD) |
|---|---|---|---|---|---|
| Lomekwi | 1,625.6 | KES 96,173 (USD 692) | KES 99,003 (USD 712) | 102.9% | 2.15 – 3.26 |
| Ekong Anaku | 1,472.9 | NGN 588,851 (USD 449) | NGN 586,762 (USD 446) | 99.6% | 0.94 – 2.18 |
| Lorengelup | 672.7 | KES 33,554 (USD 238) | KES 32,268 (USD 230) | 96.2% | 0.77 – 1.01 |

**Key findings**

1. **Billing reconciles with cash.** At every site, payments received are within 4% of the billed estimate over
   the six months. This independently confirms the tariffs and the decision to include reconstructed energy in
   revenue. Using observed energy alone, Ekong Anaku's billing would cover only about 61% of what customers paid.
2. **Currency matters for reporting.** The naira lost about 40% of its value against the US dollar during the
   period. Ekong Anaku's revenue fell 16% in naira from January to February, but about 48% in dollars. Both
   currencies are shown so that operational trends are not confused with currency effects.
3. **Different customer mix, different load shape.** Lorengelup's consumption peaks at 10:00, not in the
   evening, because its 9 commercial customers use more energy than its 40 residential customers in every
   month.

## 7. Exchange rates

| Month | KES per USD | NGN per USD |
|---|---|---|
| January 2024 | 159.69 | 941.26 |
| February 2024 | 151.84 | 1,506.84 |
| March 2024 | 137.35 | 1,510.84 |
| April 2024 | 131.57 | 1,236.56 |
| May 2024 | 131.69 | 1,435.57 |
| June 2024 | 129.36 | 1,481.77 |

- **Kenyan shilling:** Central Bank of Kenya, monthly period-average rates
  (https://www.centralbank.go.ke/statistics/exchange-rates/). These match the quarterly averages in the CBK
  Quarterly Economic Reviews (149.63 for Q1, 130.87 for Q2 2024).
- **Nigerian naira:** Central Bank of Nigeria, daily official rate
  (https://www.cbn.gov.ng/rates/ExchRateByCurrency.html), averaged over each month's business days.
- Monthly averages are used because revenue accrues throughout the month.

## 8. Key decisions

| Decision | Rationale |
|---|---|
| Real mini-grid data (Kenya, Nigeria) instead of a European household dataset | Directly relevant to off-grid energy operations; no simulated values. |
| Period January–June 2024 | Tariffs are stable, and data quality at the Kenyan sites supports reliable analysis. Tariffs before May 2023 are recorded as zero. |
| Random sample of 50 customers per site with a fixed seed | Keeps processing light and makes results exactly reproducible. |
| Reconstructed values excluded from quality and anomaly metrics | Otherwise Ekong Anaku would appear complete when most of its values are estimates. |
| Reconstructed values included in energy and revenue totals | Confirmed by the billed/collected reconciliation (section 6). |
| Days and months in local time; incomplete first and last days dropped | Matches how customers and operators experience the day. Every reported day is a full day. |
| SPIKE compares against the customer's typical in-use level, not all readings | Three quarters of readings are zero, so a plain average or median would be zero and every use of electricity would count as a spike. |
| Tariff taken as the VAT-inclusive price; net revenue derived from it | The Nigerian net price field is inconsistent with its VAT-inclusive price, and the payments data supports the VAT-inclusive figure. |
| Payment context added to ZERO_RUN | Tests whether zero-consumption periods are caused by lack of credit. Only 13 of 184 such periods ended with a top-up within a week, so this explanation is not supported and remains an open question. |
| Energy per installed kW of solar deferred to the generation analysis | Sample consumption divided by whole-site capacity would understate the real figure. |

## 9. Limitations

- **Sample, not census.** Totals cover 50 customers per site, not each site's full customer base.
- **Ekong Anaku data quality.** Most of its readings are reconstructed, so its consumption detail and anomaly
  counts are less reliable than those of the Kenyan sites.
- **Payments cover mobile money only.** Any cash top-ups are not included in collected revenue.
- **Short period.** Six months cannot show seasonal patterns over a full year.
- **SPIKE sensitivity.** For very low-consumption homes, switching on one larger appliance can exceed the 5×
  threshold. Severity levels are used to focus attention on the 549 high-severity events.
