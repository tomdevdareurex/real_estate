"""Validated records shared by parsers, pipelines, and exporters."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DiscoveryRecord(BaseModel):
    """A genuine listing URL discovered on a saved result page."""

    model_config = ConfigDict(frozen=True)

    listing_id: str
    listing_url: str
    canonical_url: str
    source_search_url: str
    source_page_number: int
    search_position: int | None = None


class DiscoveryResult(BaseModel):
    """Listings and pagination state extracted from one result page."""

    model_config = ConfigDict(frozen=True)

    listings: tuple[DiscoveryRecord, ...]
    next_page_url: str | None = None


class UnknownField(BaseModel):
    """An unmapped Lithuanian label retained for diagnostics."""

    model_config = ConfigDict(frozen=True)

    label_lt: str
    sample_value_lt: str
    listing_id: str
    listing_url: str


class ListingRecord(BaseModel):
    """Normalized listing with universal and category-specific nullable fields."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    scrape_timestamp_utc: datetime
    listing_id: str
    listing_url: str
    canonical_url: str
    property_type: str
    record_source: Literal["search", "detail"] = "detail"
    listing_type: str = "sale"
    country: str = "Lithuania"
    municipality: str | None = None
    city: str | None = None
    district: str | None = None
    neighbourhood: str | None = None
    microdistrict: str | None = None
    sector: str | None = None
    local_area: str | None = None
    street: str | None = None
    house_number: str | None = None
    full_address: str | None = None
    title_lt: str | None = None
    title_en: str | None = None
    description_lt: str | None = None
    description_en: str | None = None
    price_eur: float | None = None
    price_per_sqm_eur: float | None = None
    original_price_eur: float | None = None
    price_change_eur: float | None = None
    price_change_percent: float | None = None
    currency: str | None = "EUR"
    listing_created_date: date | None = None
    listing_updated_date: date | None = None
    listing_age_days: int | None = None
    seller_type: str | None = None
    advertiser_name: str | None = None
    agency_name: str | None = None
    listing_status: str = "active"
    source_search_url: str
    source_page_number: int
    search_position: int | None = None
    views_count: int | None = None
    saved_by_users_count: int | None = None
    latitude: float | None = None
    longitude: float | None = None
    coordinate_source: str | None = None
    coordinate_precision: str | None = None
    image_count: int | None = None
    image_urls: tuple[str, ...] | None = None
    virtual_tour_url: str | None = None
    video_url: str | None = None
    rooms: int | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    total_area_sqm: float | None = None
    living_area_sqm: float | None = None
    usable_area_sqm: float | None = None
    plot_area_original: str | None = None
    plot_area_unit: str | None = None
    plot_area_sqm: float | None = None
    plot_area_ares: float | None = None
    floor: int | None = None
    total_floors: int | None = None
    building_type: str | None = None
    house_type: str | None = None
    construction_year: int | None = None
    renovation_year: int | None = None
    completion_percent: float | None = None
    condition: str | None = None
    furnishing: str | None = None
    energy_class: str | None = None
    heating_type: str | None = None
    water_supply: str | None = None
    sewerage: str | None = None
    electricity: str | None = None
    gas: str | None = None
    parking: str | None = None
    garage: bool | None = None
    balcony: bool | None = None
    terrace: bool | None = None
    basement: bool | None = None
    storage: bool | None = None
    lift: bool | None = None
    orientation: str | None = None
    security_features: tuple[str, ...] | None = None
    ownership_type: str | None = None
    cadastral_or_legal_notes: str | None = None
    all_features_json: dict[str, Any] = Field(default_factory=dict)
    raw_attributes_json: dict[str, str] = Field(default_factory=dict)
    apartment_number: str | None = None
    apartment_total_area_sqm: float | None = None
    apartment_layout: str | None = None
    window_orientation: str | None = None
    building_renovation_status: str | None = None
    building_renovation_year: int | None = None
    building_administration_information: str | None = None
    house_total_area_sqm: float | None = None
    basement_area_sqm: float | None = None
    garage_area_sqm: float | None = None
    number_of_floors: int | None = None
    outbuildings: str | None = None
    access_road: str | None = None
    plot_purpose: str | None = None
    additional_land_features: tuple[str, ...] | None = None
    diagnostic_warnings: tuple[str, ...] = ()


class FailedUrl(BaseModel):
    """A source URL or local file that could not be processed."""

    model_config = ConfigDict(frozen=True)

    url: str
    stage: str
    error_type: str
    message: str


class ScrapeSummary(BaseModel):
    """Run-level counts written to the summary artifact."""

    model_config = ConfigDict(frozen=True)

    mode: str = "offline"
    city: str
    started_at_utc: datetime
    completed_at_utc: datetime
    apartment_files_seen: int = 0
    house_files_seen: int = 0
    apartments_exported: int = 0
    houses_exported: int = 0
    failed: int = 0
    unknown_labels: int = 0
    skipped_from_checkpoint: int = 0


class OnlineScrapeSummary(BaseModel):
    """Run-level counts for bounded remote retrieval."""

    model_config = ConfigDict(frozen=True)

    mode: Literal["online"] = "online"
    city: str
    started_at_utc: datetime
    completed_at_utc: datetime
    search_pages_fetched: int = 0
    listings_discovered: int = 0
    detail_pages_fetched: int = 0
    apartments_exported: int = 0
    houses_exported: int = 0
    failed: int = 0
    unknown_labels: int = 0
    skipped_existing: int = 0
    # Listings the origin refused during the main pass and were asked for again after the
    # cooldown. `failed` counts only what was still failing once the retry pass finished.
    deferred_retries_attempted: int = 0
    deferred_retries_recovered: int = 0
