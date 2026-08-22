"""Stable package constants."""

DEFAULT_OUTPUT_DIRECTORY = "data/processed"
DEFAULT_CACHE_DIRECTORY = "data/raw/cache"
DEFAULT_CHECKPOINT = "data/interim/checkpoints/offline.json"
DEFAULT_RUN_CONFIG = "config/scrape.yaml"
SUPPORTED_PROPERTY_TYPES = ("apartments", "houses")
MAX_HTML_FILE_BYTES = 10 * 1024 * 1024
MAX_HTML_FILES = 10_000
MAX_CONFIG_BYTES = 1024 * 1024
MAX_JSON_LD_BYTES = 1024 * 1024

# Upper bounds on a single run's traversal. These are guardrails against a typo turning into
# a runaway crawl, NOT limits the origin imposes: pagination follows the "next page" link the
# site itself serves, so it continues for as long as Aruodas offers one.
#
# They were 20 and 500, which capped a category at roughly one page-1-to-20 sweep - about 500
# listings, and exactly where a full Vilnius export stalled. Raised so a whole city fits in
# one configuration. What actually bounds a run is the per-IP request budget, plus
# max_cooldowns / max_runtime_seconds.
#
# Deep pagination is the weaker half: search results re-sort between requests, so listings can
# be missed or seen twice the further in you go. Partitioning the search into several narrower
# URLs in cities.yaml (by district, price band, room count) is more reliable than one very
# deep walk, and each partition then sits well inside these bounds.
MAX_SEARCH_PAGES = 500
MAX_DETAIL_FETCHES_PER_CATEGORY = 20_000

# Codec for every CSV artifact, on both the read and the write side.
#
# Excel on Windows decodes a .csv with the legacy ANSI codepage unless the file opens with a
# BOM, which renders correct Lithuanian as mojibake - "Visorių g." shown as "VisoriÅ³ g.".
# The bytes were never wrong; only Excel's guess was, and a BOM removes the guess.
#
# Readers must use this too. The exports are parsed back to merge new rows in, and plain
# "utf-8" would leave the BOM attached to the first header name, so every row would look
# like a new listing and a merge would duplicate instead of update. "utf-8-sig" is also
# tolerant of files that have no BOM, so exports written before this change still load.
#
# JSON artifacts deliberately stay on plain UTF-8: a BOM is invalid to many JSON parsers,
# and nothing opens those in a spreadsheet.
CSV_ENCODING = "utf-8-sig"
