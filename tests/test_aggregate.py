"""Aggregation: local days, tariff lookup, VAT split and USD conversion."""

import pandas as pd
import pytest

from aggregate import add_revenue, add_tariff, daily_per_meter, to_usd

NAN = float("nan")


def make_readings(start_local: str, n_slots: int, value: float = 0.01, observed: bool = True):
    """n_slots 15-minute readings for one meter, starting at a LOCAL time."""
    local = pd.date_range(start_local, periods=n_slots, freq="15min")
    return pd.DataFrame({
        "meter_id": "LOM-001", "site": "Lomekwi", "slot_start_local": local,
        "energy_kwh": value, "energy_kwh_observed": value if observed else NAN,
    })


def test_full_day_is_summed():
    daily = daily_per_meter(make_readings("2024-03-01 00:00", 96, value=0.01))
    assert len(daily) == 1
    assert daily.loc[0, "kwh"] == pytest.approx(0.96)
    assert daily.loc[0, "slots_observed"] == 96


def test_partial_edge_days_are_dropped():
    # Kenya: the UTC window starts at 03:00 local on 1 Jan -> 1 Jan has 84 slots only.
    daily = daily_per_meter(make_readings("2024-01-01 03:00", 84 + 96))
    assert list(daily["date_local"].dt.strftime("%Y-%m-%d")) == ["2024-01-02"]


def test_reconstructed_energy_counts_in_kwh_but_not_in_observed():
    daily = daily_per_meter(make_readings("2024-03-01 00:00", 96, value=0.01, observed=False))
    assert daily.loc[0, "kwh"] == pytest.approx(0.96)
    assert pd.isna(daily.loc[0, "kwh_observed"])
    assert daily.loc[0, "slots_observed"] == 0


def test_day_with_no_value_at_all_has_empty_kwh_not_zero():
    daily = daily_per_meter(make_readings("2024-03-01 00:00", 96, value=NAN, observed=False))
    assert pd.isna(daily.loc[0, "kwh"])


def test_tariff_in_force_on_the_day_is_used():
    daily = pd.DataFrame({"meter_id": "LOM-001", "site": "Lomekwi",
                          "date_local": pd.to_datetime(["2024-01-31", "2024-02-01", "2024-02-02"]),
                          "kwh": 1.0})
    meters = pd.DataFrame({"meter_id": ["LOM-001"], "tariff_class": ["Residential"]})
    tariffs = pd.DataFrame({
        "site": "Lomekwi", "tariff_class": ["Residential", "Residential", "Commercial"],
        "startingDatetime": ["2023-05-01 00:00:00", "2024-02-01 00:00:00", "2024-01-15 00:00:00"],
        "totalRate": [59.16, 70.0, 99.0], "vatRate": 0.16,
    })
    prices = add_tariff(daily, meters, tariffs)["price_per_kwh"].tolist()
    assert prices == [59.16, 70.0, 70.0]   # never the Commercial price, new price from its start day


def test_vat_is_taken_out_of_the_vat_inclusive_price():
    daily = pd.DataFrame({"kwh": [2.0], "price_per_kwh": [58.0], "vat_rate": [0.16]})
    out = add_revenue(daily)
    assert out.loc[0, "revenue_lcy"] == pytest.approx(116.0)
    assert out.loc[0, "revenue_excl_vat_lcy"] == pytest.approx(100.0)


def test_usd_uses_the_rate_of_the_same_month_and_currency():
    table = pd.DataFrame({"month": ["2024-01", "2024-02"], "currency": "KES", "revenue_lcy": [1597.0, 1518.4]})
    fx = pd.DataFrame({"month": ["2024-01", "2024-02", "2024-01"], "currency": ["KES", "KES", "NGN"],
                       "local_per_usd": [159.7, 151.84, 941.26]})
    out = to_usd(table, fx, ["revenue_lcy"])
    assert out["revenue_usd"].round(2).tolist() == [10.0, 10.0]


def test_month_without_rate_gives_empty_usd():
    table = pd.DataFrame({"month": ["2024-07"], "currency": "KES", "revenue_lcy": [100.0]})
    fx = pd.DataFrame({"month": ["2024-01"], "currency": ["KES"], "local_per_usd": [159.69]})
    assert pd.isna(to_usd(table, fx, ["revenue_lcy"]).loc[0, "revenue_usd"])
