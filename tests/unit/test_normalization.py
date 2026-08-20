from datetime import date

import pytest

from aruodas_scraper.normalization.dates import parse_iso_date
from aruodas_scraper.normalization.numbers import parse_decimal, parse_integer
from aruodas_scraper.normalization.units import parse_plot_area
from aruodas_scraper.validation.coordinates import validate_coordinates


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [("148 500 €", 148500.0), ("45,14 m²", 45.14), (None, None), ("—", None)],
)
def test_parse_decimal_handles_lithuanian_format(raw: str | None, expected: float | None) -> None:
    assert parse_decimal(raw) == expected


@pytest.mark.unit
def test_parse_integer_does_not_turn_missing_value_into_zero() -> None:
    assert parse_integer(None) is None
    assert parse_integer("1 234/12") == 1234


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "unit", "sqm", "ares"),
    [("10 a", "a", 1000.0, 10.0), ("0,25 ha", "ha", 2500.0, 25.0), ("850 m²", "m²", 850.0, 8.5)],
)
def test_parse_plot_area_preserves_unit_and_conversions(
    raw: str, unit: str, sqm: float, ares: float
) -> None:
    parsed = parse_plot_area(raw)

    assert parsed.original == raw
    assert parsed.unit == unit
    assert parsed.square_metres == sqm
    assert parsed.ares == ares


@pytest.mark.unit
def test_parse_iso_date_returns_none_for_unavailable_value() -> None:
    assert parse_iso_date("2026-08-17") == date(2026, 8, 17)
    assert parse_iso_date(None) is None


@pytest.mark.unit
def test_validate_coordinates_rejects_values_outside_lithuania() -> None:
    valid = validate_coordinates(54.7, 25.3)
    invalid = validate_coordinates(40.7, -74.0)

    assert valid.latitude == 54.7
    assert valid.longitude == 25.3
    assert valid.warning is None
    assert invalid.latitude is None
    assert invalid.longitude is None
    assert "outside Lithuania" in (invalid.warning or "")
