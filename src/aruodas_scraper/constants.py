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
