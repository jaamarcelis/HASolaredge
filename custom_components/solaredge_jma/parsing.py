"""Defensive parsers for SolarEdge Monitoring API v2 responses.

The public documentation describes the envelopes and metric names, but not every
nesting level of every response. These helpers therefore look for values by metric
name wherever they appear in a response, instead of relying on a fixed structure.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date
from typing import Any

# Keys that may hold the timestamp of a sample
TIMESTAMP_KEYS = ("date", "timestamp", "time", "startTime", "start", "from")
# Keys that may hold the value of a sample
VALUE_KEYS = ("value", "val")
# Keys that may name the metric of a list of samples
METRIC_NAME_KEYS = ("metric", "name", "type", "measurement")
# Keys that may hold a device serial number
SERIAL_KEYS = ("serialNumber", "serial", "SN", "sn")
# Keys that may hold the unit of the values in an object
UNIT_KEYS = ("unit", "units")

ENERGY_FACTORS = {"WH": 1.0, "KWH": 1000.0, "MWH": 1_000_000.0}
POWER_FACTORS = {"W": 1.0, "KW": 1000.0, "MW": 1_000_000.0}


@dataclass(frozen=True, slots=True)
class Sample:
    """A single metric value."""

    metric: str
    value: float
    timestamp: str | None
    serial: str | None
    unit: str | None


def _as_number(value: Any) -> float | None:
    """Return value as float, or None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _first(obj: dict[str, Any], keys: Iterable[str]) -> Any:
    """Return the first non-None value of keys in obj."""
    for key in keys:
        if obj.get(key) is not None:
            return obj[key]
    return None


def _normalize(name: str) -> str:
    """Normalize a metric or key name for comparison."""
    return name.replace("_", "").replace("-", "").lower()


def scale(value: float, unit: str | None, factors: dict[str, float]) -> float:
    """Scale value to the base unit (Wh or W)."""
    if unit is None:
        return value
    return value * factors.get(unit.replace(" ", "").upper(), 1.0)


def iter_samples(data: Any, metrics: Iterable[str]) -> Iterator[Sample]:
    """Yield all samples of the requested metrics found anywhere in data.

    Supported shapes (at any depth):
    - {"date": ..., "productionPower": 1.0, ...}       (metric as key)
    - {"metric": "productionPower", "values": [...]}    (metric as name)
    - {"productionPower": [{"date": ..., "value": 1}]}  (metric with sample list)
    """
    wanted = {_normalize(metric): metric for metric in metrics}

    def walk(
        obj: Any, serial: str | None, unit: str | None, metric: str | None
    ) -> Iterator[Sample]:
        if isinstance(obj, list):
            for item in obj:
                yield from walk(item, serial, unit, metric)
            return
        if not isinstance(obj, dict):
            return

        if (found_serial := _first(obj, SERIAL_KEYS)) is not None:
            serial = str(found_serial)
        if isinstance(found_unit := _first(obj, UNIT_KEYS), str):
            unit = found_unit
        timestamp = _first(obj, TIMESTAMP_KEYS)
        timestamp = str(timestamp) if timestamp is not None else None

        # {"metric": "productionPower", "values": [...]}
        name = _first(obj, METRIC_NAME_KEYS)
        if isinstance(name, str) and _normalize(name) in wanted:
            metric = wanted[_normalize(name)]

        # Sample of a list that belongs to a known metric
        if metric is not None:
            value = _as_number(_first(obj, VALUE_KEYS))
            if value is not None:
                yield Sample(metric, value, timestamp, serial, unit)

        for key, value in obj.items():
            normalized = _normalize(key)
            if normalized in wanted:
                if (number := _as_number(value)) is not None:
                    yield Sample(wanted[normalized], number, timestamp, serial, unit)
                elif isinstance(value, dict | list):
                    yield from walk(value, serial, unit, wanted[normalized])
            elif isinstance(value, dict | list):
                yield from walk(value, serial, unit, metric)

    yield from walk(data, None, None, None)


def latest_by_serial(samples: Iterable[Sample]) -> dict[tuple[str, str | None], Sample]:
    """Return the most recent sample per (metric, serial)."""
    latest: dict[tuple[str, str | None], Sample] = {}
    for sample in samples:
        key = (sample.metric, sample.serial)
        current = latest.get(key)
        if (
            current is None
            or current.timestamp is None
            or (sample.timestamp is not None and sample.timestamp >= current.timestamp)
        ):
            latest[key] = sample
    return latest


def sum_latest(
    samples: Iterable[Sample], metric: str, factors: dict[str, float]
) -> float | None:
    """Sum the latest value of metric over all devices."""
    values = [
        scale(sample.value, sample.unit, factors)
        for (name, _serial), sample in latest_by_serial(samples).items()
        if name == metric
    ]
    return sum(values) if values else None


def sum_all(
    samples: Iterable[Sample], metric: str, factors: dict[str, float]
) -> float | None:
    """Sum all values of metric over all devices and intervals."""
    values = [
        scale(sample.value, sample.unit, factors)
        for sample in samples
        if sample.metric == metric
    ]
    return sum(values) if values else None


def daily_values(data: Any) -> dict[date, float]:
    """Return {date: value in Wh} from a measurements envelope with daily values.

    The envelope is {period, unit, resolution, values: [...]}. Timestamps are in
    the site's local time, so the date part of the timestamp is the local date.
    """
    if not isinstance(data, dict):
        return {}
    unit = _first(data, UNIT_KEYS)
    result: dict[date, float] = {}
    values = data.get("values")
    if not isinstance(values, list):
        return result
    for item in values:
        if not isinstance(item, dict):
            continue
        timestamp = _first(item, TIMESTAMP_KEYS)
        value = _as_number(_first(item, VALUE_KEYS))
        if timestamp is None or value is None:
            continue
        try:
            day = date.fromisoformat(str(timestamp)[:10])
        except ValueError:
            continue
        item_unit = _first(item, UNIT_KEYS)
        result[day] = result.get(day, 0.0) + scale(
            value, item_unit if isinstance(item_unit, str) else unit, ENERGY_FACTORS
        )
    return result


def latest_value(data: Any, factors: dict[str, float]) -> float | None:
    """Return the most recent non-null value of a measurements envelope."""
    if not isinstance(data, dict) or not isinstance(data.get("values"), list):
        return None
    unit = _first(data, UNIT_KEYS)
    latest: tuple[str, float] | None = None
    for item in data["values"]:
        if not isinstance(item, dict):
            continue
        value = _as_number(_first(item, VALUE_KEYS))
        if value is None:
            continue
        timestamp = str(_first(item, TIMESTAMP_KEYS) or "")
        if latest is None or timestamp >= latest[0]:
            latest = (timestamp, value)
    if latest is None:
        return None
    return scale(latest[1], unit if isinstance(unit, str) else None, factors)


def find_number(data: Any, keys: Iterable[str]) -> float | None:
    """Return the first numeric value of one of keys, searched depth first."""
    wanted = [_normalize(key) for key in keys]
    if isinstance(data, dict):
        normalized = {_normalize(key): value for key, value in data.items()}
        for key in wanted:
            if (number := _as_number(normalized.get(key))) is not None:
                return number
        for value in data.values():
            if (number := find_number(value, keys)) is not None:
                return number
    elif isinstance(data, list):
        for item in data:
            if (number := find_number(item, keys)) is not None:
                return number
    return None


def lifetime_energy(data: Any) -> float | None:
    """Return the site lifetime energy in Wh."""
    if isinstance(data, dict) and isinstance(data.get("values"), list):
        return latest_value(data, ENERGY_FACTORS)
    value = find_number(
        data, ("lifetimeEnergy", "lifeTimeEnergy", "energy", "value", "total")
    )
    if value is None:
        return None
    unit = _first(data, UNIT_KEYS) if isinstance(data, dict) else None
    return scale(value, unit if isinstance(unit, str) else None, ENERGY_FACTORS)


def unwrap_site(data: Any) -> dict[str, Any]:
    """Return the site object of a GET /sites/{id} response."""
    if isinstance(data, dict):
        site = data.get("site")
        if isinstance(site, dict):
            return site
        if isinstance(site, list) and site and isinstance(site[0], dict):
            return site[0]
        return data
    return {}


def device_list(data: Any) -> list[dict[str, Any]]:
    """Return the list of device objects of a GET /sites/{id}/devices response."""
    if isinstance(data, list):
        if all(isinstance(item, dict) for item in data) and any(
            _first(item, SERIAL_KEYS) is not None or "type" in item for item in data
        ):
            return data
        for item in data:
            if found := device_list(item):
                return found
        return []
    if isinstance(data, dict):
        for value in data.values():
            if isinstance(value, dict | list) and (found := device_list(value)):
                return found
    return []


def device_serial(device: dict[str, Any]) -> str | None:
    """Return the serial number of a device object."""
    serial = _first(device, SERIAL_KEYS)
    return str(serial) if serial is not None else None


def device_type(device: dict[str, Any]) -> str:
    """Return the upper case type of a device object."""
    return str(device.get("type") or device.get("deviceType") or "").upper()
