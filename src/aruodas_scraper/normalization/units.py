"""Area and plot-unit normalization."""

from dataclasses import dataclass

from aruodas_scraper.normalization.numbers import parse_decimal


@dataclass(frozen=True, slots=True)
class PlotArea:
    """Plot area retaining the displayed value and normalized conversions."""

    original: str
    unit: str | None
    square_metres: float | None
    ares: float | None


def parse_plot_area(value: str) -> PlotArea:
    """Parse square metres, ares, or hectares into separate values."""
    amount = parse_decimal(value)
    normalized = value.lower().replace("²", "2")
    unit: str | None
    multiplier: float | None
    if "ha" in normalized:
        unit, multiplier = "ha", 10000.0
    elif " m2" in normalized or normalized.endswith("m2"):
        unit, multiplier = "m²", 1.0
    elif " a" in normalized or normalized.endswith("a"):
        unit, multiplier = "a", 100.0
    else:
        unit, multiplier = None, None
    square_metres = amount * multiplier if amount is not None and multiplier is not None else None
    return PlotArea(
        original=value,
        unit=unit,
        square_metres=square_metres,
        ares=square_metres / 100 if square_metres is not None else None,
    )
