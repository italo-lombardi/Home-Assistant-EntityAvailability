"""Constants for the Entity Availability integration."""

from typing import Literal

DOMAIN = "entity_availability"

SignalQuality = Literal["good", "ok", "poor"]

# Config flow
CONF_GROUP_NAME = "group_name"
CONF_ENTITIES = "entities"
CONF_BAD_STATES = "bad_states"
CONF_COOLDOWN = "cooldown"
CONF_STALENESS_THRESHOLD = "staleness_threshold"
CONF_STALENESS_USE_LAST_UPDATED = "staleness_use_last_updated"
CONF_BATTERY_THRESHOLD = "battery_threshold"
CONF_BATTERY_ENTITY_MAP = "battery_entity_map"
CONF_SIGNAL_ENABLED = "signal_enabled"
CONF_SIGNAL_ENTITY_MAP = "signal_entity_map"
CONF_AVAILABILITY_WINDOWS = "availability_windows"
CONF_USE_DEVICE_NAMES = "use_device_names"
CONF_COLLAPSE_DEVICES = "collapse_devices"
CONF_NON_ESSENTIAL_ENTITIES = "non_essential_entities"

# Entry types
CONF_ENTRY_TYPE = "entry_type"
ENTRY_TYPE_GROUP = "group"
ENTRY_TYPE_COMBINED = "combined_group"
CONF_COMBINED_GROUPS = "combined_groups"

# Defaults
DEFAULT_NAME = "Entity Availability"
DEFAULT_BAD_STATES = ["unavailable", "unknown"]
DEFAULT_COOLDOWN = 60  # seconds
DEFAULT_STALENESS_THRESHOLD = 0  # disabled
DEFAULT_STALENESS_USE_LAST_UPDATED = False  # last_changed preserves prior behavior
DEFAULT_BATTERY_THRESHOLD = 20  # percent
DEFAULT_SIGNAL_ENABLED = False
DEFAULT_AVAILABILITY_WINDOWS = ["today", "7d"]
DEFAULT_USE_DEVICE_NAMES = False
DEFAULT_COLLAPSE_DEVICES = False

# Signal strength thresholds per network type.
# Each entry: {"label": str, "unit": str, "good": int, "ok": int, "higher_is_better": bool}
# higher_is_better=False (dBm): level >= good → green; level >= ok → yellow; below ok → red
# higher_is_better=True  (LQI/%): level >= good → green; level >= ok → yellow; below ok → red
# To add a new type: append an entry here — the config flow, coordinator, and card pick it up automatically.
SIGNAL_NETWORK_TYPES: dict[str, dict[str, int | str | bool]] = {
    "5g": {
        "label": "5G",
        "unit": "dBm",
        "good": -80,
        "ok": -100,
        "higher_is_better": False,
    },
    "bluetooth": {
        "label": "Bluetooth",
        "unit": "dBm",
        "good": -70,
        "ok": -85,
        "higher_is_better": False,
    },
    "generic": {
        "label": "Generic/RSSI",
        "unit": "dBm",
        "good": -60,
        "ok": -80,
        "higher_is_better": False,
    },
    "lorawan": {
        "label": "LoRaWAN",
        "unit": "dBm",
        "good": -100,
        "ok": -115,
        "higher_is_better": False,
    },
    "lte": {
        "label": "LTE/4G",
        "unit": "dBm",
        "good": -80,
        "ok": -90,
        "higher_is_better": False,
    },
    "percent": {
        "label": "Percentage",
        "unit": "%",
        "good": 70,
        "ok": 40,
        "higher_is_better": True,
    },
    "thread": {
        "label": "Thread",
        "unit": "dBm",
        "good": -70,
        "ok": -85,
        "higher_is_better": False,
    },
    "wifi": {
        "label": "Wi-Fi",
        "unit": "dBm",
        "good": -67,
        "ok": -80,
        "higher_is_better": False,
    },
    "zigbee_lqi": {
        "label": "Zigbee (LQI)",
        "unit": "LQI",
        "good": 201,
        "ok": 51,
        "higher_is_better": True,
    },
    "zigbee_rssi": {
        "label": "Zigbee (RSSI/dBm)",
        "unit": "dBm",
        "good": -70,
        "ok": -85,
        "higher_is_better": False,
    },
    "zwave": {
        "label": "Z-Wave",
        "unit": "dBm",
        "good": -70,
        "ok": -85,
        "higher_is_better": False,
    },
}
AVAILABLE_WINDOWS = ["today", "3d", "5d", "7d"]

# Storage
STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = "entity_availability"
BUCKET_INTERVAL = 300  # 5 minutes per bucket
BUCKETS_MAX = 2016  # 7 days * 24 hours * 12 buckets/hour

# Update interval for coordinator
SCAN_INTERVAL = 30  # seconds

# Grace period after HA startup before new offline transitions are allowed
STARTUP_GRACE_PERIOD = 60  # seconds

# Recovery window for recently_recovered / recently_offline sensors
CONF_RECOVERY_WINDOW = "recovery_window"
DEFAULT_RECOVERY_WINDOW = 5  # minutes

# Services
SERVICE_RESET_STATISTICS = "reset_statistics"

# Bus events fired on entity availability transitions
EVENT_OFFLINE = "entity_availability_offline"
EVENT_RECOVERED = "entity_availability_recovered"
EVENT_LOW_BATTERY = "entity_availability_low_battery"
EVENT_BATTERY_OK = "entity_availability_battery_ok"
EVENT_STALE = "entity_availability_stale"
EVENT_STALE_RECOVERED = "entity_availability_stale_recovered"
EVENT_POOR_SIGNAL = "entity_availability_poor_signal"
EVENT_SIGNAL_OK = "entity_availability_signal_ok"

# Sentinel area name for entities with no HA area assigned.
# Parentheses signal "not a real area" and avoid colliding with user-created area names.
NO_AREA_SENTINEL = "(No Area)"
