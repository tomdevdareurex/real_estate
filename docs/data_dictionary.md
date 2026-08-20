# Data Dictionary

Both category CSVs use one stable English schema. Empty CSV cells mean the source did not display
the value; zero is emitted only when explicitly displayed.

## Identity and provenance

`scrape_timestamp_utc`, `listing_id`, `listing_url`, `canonical_url`, `property_type`,
`listing_type`, `source_search_url`, `source_page_number`, `search_position`, `listing_status`.

## Location

`country`, `municipality`, `city`, `district`, `neighbourhood`, `microdistrict`, `sector`,
`local_area`, `street`, `house_number`, `full_address`. Lithuanian proper names are preserved.

## Text and price

`title_lt`, `title_en`, `description_lt`, `description_en`, `price_eur`,
`price_per_sqm_eur`, `original_price_eur`, `price_change_eur`, `price_change_percent`, `currency`.
English long text remains null unless a provider is configured.
Email addresses and Lithuanian phone numbers embedded in source text are redacted by default.

## Dates and engagement

`listing_created_date`, `listing_updated_date`, `listing_age_days`, `views_count`,
`saved_by_users_count`.

## Geography and media

`latitude`, `longitude`, `coordinate_source`, `coordinate_precision`, `image_count`,
`image_urls`, `virtual_tour_url`, `video_url`. Invalid coordinates become null with a diagnostic.
Image URLs are disabled by default.

## Areas and physical attributes

House floor area and plot area are never combined. Relevant columns include `total_area_sqm`,
`apartment_total_area_sqm`, `house_total_area_sqm`, `living_area_sqm`, `usable_area_sqm`,
`basement_area_sqm`, `garage_area_sqm`, `plot_area_original`, `plot_area_unit`,
`plot_area_sqm`, and `plot_area_ares`.

Remaining columns cover rooms, floors, construction, renovation, condition, utilities, energy,
parking, features, legal notes, apartment fields, and house fields. `raw_attributes_json` retains
every displayed label/value pair. `all_features_json` stores normalized Boolean features.
