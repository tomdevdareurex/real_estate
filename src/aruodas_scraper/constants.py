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
