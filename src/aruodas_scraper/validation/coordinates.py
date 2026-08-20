"""Lithuania coordinate bounds validation."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CoordinateValidation:
    """Validated coordinates and an optional warning."""

    latitude: float | None
    longitude: float | None
    warning: str | None


def validate_coordinates(latitude: float | None, longitude: float | None) -> CoordinateValidation:
    """Validate coordinates against generous Lithuania geographic bounds."""
    if latitude is None or longitude is None:
        return CoordinateValidation(None, None, "Coordinate pair is incomplete.")
    if not (53.8 <= latitude <= 56.5 and 20.8 <= longitude <= 26.9):
        return CoordinateValidation(None, None, "Coordinates are outside Lithuania bounds.")
    return CoordinateValidation(latitude, longitude, None)
