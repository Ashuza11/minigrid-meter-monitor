"""Energy balance: power -> energy, coverage rule and the two ratios."""

import pandas as pd
import pytest

from generation import daily_balance, meter_totals_local, monthly_balance, power_to_slot_energy, total_power

NAN = float("nan")


def make_gen(times, pv, load, genset=None):
    """Monitoring rows for one site with two solar chargers and two AC phases."""
    df = pd.DataFrame({
        "site": "Lomekwi", "timestamp_local": pd.to_datetime(times),
        "Solar_Charger_0_PV_power": [p / 2 for p in pv], "Solar_Charger_1_PV_power": [p / 2 for p in pv],
        "System_overview_AC_Consumption_L1": [x / 2 for x in load],
        "System_overview_AC_Consumption_L2": [x / 2 for x in load],
    })
    if genset is not None:
        df["System_overview_Genset_L1"] = genset
    return df


def test_device_columns_are_added_up():
    power = total_power(make_gen(["2024-03-01 12:00"], pv=[3000], load=[1000], genset=[NAN]))
    assert power.loc[0, "pv_w"] == 3000
    assert power.loc[0, "load_w"] == 1000
    assert power.loc[0, "genset_w"] == 0      # generator not recorded = not running


def test_power_becomes_energy():
    # 2,000 W held for one 15-minute slot = 2 kW x 0.25 h = 0.5 kWh
    energy = power_to_slot_energy(total_power(make_gen(["2024-03-01 12:00"], pv=[2000], load=[2000])))
    assert energy.loc[0, "pv_kwh"] == pytest.approx(0.5)


def test_several_samples_in_a_slot_are_averaged():
    times = ["2024-03-01 12:00", "2024-03-01 12:05", "2024-03-01 12:10"]
    energy = power_to_slot_energy(total_power(make_gen(times, pv=[1000, 2000, 3000], load=[0, 0, 0])))
    assert len(energy) == 1
    assert energy.loc[0, "pv_kwh"] == pytest.approx(0.5)   # mean 2,000 W


def test_empty_slots_stay_visible():
    times = ["2024-03-01 12:00", "2024-03-01 13:00"]
    energy = power_to_slot_energy(total_power(make_gen(times, pv=[1000, 1000], load=[500, 500])))
    assert len(energy) == 5                    # 12:00, 12:15, 12:30, 12:45, 13:00
    assert energy["pv_kwh"].isna().sum() == 3


def test_meter_slots_move_from_utc_to_local_time():
    totals = pd.DataFrame({"site": ["Lomekwi"], "slot_start": ["2024-03-01 09:00:00+00:00"],
                           "energy_kwh": [0.2], "energy_kwh_observed": [0.2], "meters": [50]})
    sites = pd.DataFrame({"site": ["Lomekwi"], "utc_offset_hours": [3]})
    out = meter_totals_local(totals, sites)
    assert out.loc[0, "slot_local"] == pd.Timestamp("2024-03-01 12:00")


def one_day(load_kwh=1.0, metered_kwh=0.75, monitored=96):
    """Slot tables for one full local day; the first `monitored` slots have monitoring."""
    slots = pd.date_range("2024-03-01", periods=96, freq="15min")
    watched = [i < monitored for i in range(96)]
    energy = pd.DataFrame({"site": "Lomekwi", "slot_local": slots,
                           "pv_kwh": [1.0 if w else NAN for w in watched],
                           "load_kwh": [load_kwh if w else NAN for w in watched],
                           "genset_kwh": 0.0, "battery_soc_pct": 90.0})
    meters = pd.DataFrame({"site": "Lomekwi", "slot_local": slots, "metered_kwh": metered_kwh,
                           "metered_kwh_observed": metered_kwh, "meters": 50})
    return energy, meters


def test_meter_total_uses_only_monitored_slots():
    energy, meters = one_day(monitored=90)
    daily = daily_balance(energy, meters)
    assert daily.loc[0, "load_kwh"] == pytest.approx(90.0)
    assert daily.loc[0, "metered_kwh"] == pytest.approx(90 * 0.75)   # not all 96 slots


def test_day_needs_90_percent_coverage():
    assert bool(daily_balance(*one_day(monitored=87)).loc[0, "included"])        # 90.6%
    assert not bool(daily_balance(*one_day(monitored=86)).loc[0, "included"])    # 89.6%


def test_monthly_ratios():
    daily = daily_balance(*one_day(load_kwh=1.0, metered_kwh=0.75, monitored=96))
    sites = pd.DataFrame({"site": ["Lomekwi"], "pv_kwp": [24.0]})
    month = monthly_balance(daily, sites).iloc[0]
    assert month["metered_share_of_load_pct"] == pytest.approx(75.0)
    assert month["pv_kwh_per_kwp_day"] == pytest.approx(96.0 / 24.0)   # 96 kWh in one day / 24 kWp


def test_partial_local_day_is_dropped():
    energy, meters = one_day()
    daily = daily_balance(energy, meters.iloc[12:])     # meter data starts at 03:00 local
    assert daily.empty
