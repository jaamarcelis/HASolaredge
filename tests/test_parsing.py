"""Tests for the defensive response parsers."""

from datetime import date

from custom_components.solaredge_jma.coordinator import (
    SolarEdgePowerFlowCoordinator,
)
from custom_components.solaredge_jma.parsing import (
    ENERGY_FACTORS,
    POWER_FACTORS,
    daily_values,
    device_list,
    iter_samples,
    lifetime_energy,
    sum_all,
    sum_latest,
    unwrap_site,
)


def test_metric_as_key() -> None:
    """Test samples with the metric names as keys."""
    data = {
        "meters": [
            {
                "serialNumber": "A",
                "values": [
                    {"date": "2026-11-05T10:00:00+01:00", "productionPower": 1.0},
                    {"date": "2026-11-05T10:15:00+01:00", "productionPower": 2.0},
                ],
            },
            {
                "serialNumber": "B",
                "values": [{"date": "2026-11-05T10:15:00+01:00", "productionPower": 3}],
            },
        ]
    }
    samples = list(iter_samples(data, ["productionPower"]))
    assert len(samples) == 3
    assert sum_latest(samples, "productionPower", POWER_FACTORS) == 5.0
    assert sum_all(samples, "productionPower", POWER_FACTORS) == 6.0


def test_metric_as_name_with_unit() -> None:
    """Test samples in a list named by metric, in kW."""
    data = {
        "telemetry": [
            {
                "metric": "productionPower",
                "unit": "kW",
                "values": [
                    {"date": "2026-11-05T10:00:00+01:00", "value": 1.5},
                    {"date": "2026-11-05T10:15:00+01:00", "value": None},
                ],
            }
        ]
    }
    samples = list(iter_samples(data, ["productionPower"]))
    assert len(samples) == 1
    assert sum_latest(samples, "productionPower", POWER_FACTORS) == 1500.0


def test_metric_with_sample_list() -> None:
    """Test a metric key holding a list of samples."""
    data = {
        "serialNumber": "BAT",
        "chargeEnergy": [
            {"date": "2026-11-05T10:00:00+01:00", "value": 100},
            {"date": "2026-11-05T10:15:00+01:00", "value": 50},
        ],
    }
    samples = list(iter_samples(data, ["chargeEnergy"]))
    assert {sample.serial for sample in samples} == {"BAT"}
    assert sum_all(samples, "chargeEnergy", ENERGY_FACTORS) == 150


def test_daily_values_units() -> None:
    """Test daily values in kWh are converted to Wh."""
    data = {
        "unit": "KWH",
        "values": [
            {"date": "2026-11-04T00:00:00+01:00", "value": 1.5},
            {"date": "2026-11-05T00:00:00+01:00", "value": None},
        ],
    }
    assert daily_values(data) == {date(2026, 11, 4): 1500.0}


def test_lifetime_energy_shapes() -> None:
    """Test the lifetime energy parser."""
    assert lifetime_energy({"energy": 10, "unit": "kWh"}) == 10_000
    assert lifetime_energy({"lifetimeEnergy": {"value": 5}}) == 5
    assert (
        lifetime_energy({"unit": "Wh", "values": [{"date": "2026-11-05", "value": 7}]})
        == 7
    )


def test_unwrap_site_and_devices() -> None:
    """Test site and device list unwrapping."""
    assert unwrap_site({"site": {"siteId": 1}}) == {"siteId": 1}
    assert unwrap_site({"siteId": 1}) == {"siteId": 1}
    devices = [{"serialNumber": "A", "type": "INVERTER"}]
    assert device_list(devices) == devices
    assert device_list({"devices": devices}) == devices
    assert device_list({"devices": {"count": 1, "device": devices}}) == devices


def test_power_flow_v1_shape() -> None:
    """Test the power flow parser with the v1 shape."""
    data = SolarEdgePowerFlowCoordinator.parse_power_flow(
        {
            "siteCurrentPowerFlow": {
                "unit": "kW",
                "connections": [
                    {"from": "PV", "to": "Load"},
                    {"from": "LOAD", "to": "Grid"},
                ],
                "GRID": {"status": "Active", "currentPower": 1.2},
                "LOAD": {"status": "Active", "currentPower": 0.8},
                "PV": {"status": "Active", "currentPower": 2.0},
                "STORAGE": {"currentPower": 0.0, "chargeLevel": 61},
            }
        }
    )
    assert data["solar_power"] == 2000.0
    assert data["power_consumption"] == 800.0
    assert data["grid_power"] == 1200.0
    assert data["grid_flow_direction"] == "export"
    assert data["storage_level"] == 61
