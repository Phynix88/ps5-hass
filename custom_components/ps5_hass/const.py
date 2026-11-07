"""Constants for ps5-hass."""

from typing import Final

DOMAIN: Final = "ps5_hass"
DEFAULT_PORT: Final = 8181
DDP_PORT: Final = 9302
STATE_INTERVAL: Final = 10  # s
APPS_INTERVAL: Final = 600  # s
PLAYTIME_INTERVAL: Final = 60  # s
HOLD_SECONDS: Final = 30  # keep last data this long before asking DDP
CONF_TOKEN: Final = "token"
CONF_CLIENT_ID: Final = "client_id"
CONF_INFO: Final = "info"
OPT_SOURCES: Final = "sources"  # "all" | "games"
OPT_FAVORITES: Final = "favorites"
OPT_FAVORITE_NAMES: Final = "favorite_names"  # title id -> name at selection time
LABEL: Final = "Home Assistant"
MANUFACTURER: Final = "Sony Interactive Entertainment"
DEVICE_NAME: Final = "PS5"
PAYLOAD_RELEASE_URL: Final = (
    "https://raw.githubusercontent.com/Phynix88/ps5-hass-payload/main/payloads.json"
)
PAYLOAD_RELEASE_INTERVAL: Final = 6 * 3600  # s
