"""Constants for the Entity Availability integration."""

DOMAIN = "entity_availability"

# Config flow
CONF_GROUP_NAME = "group_name"
CONF_ENTITIES = "entities"
CONF_BAD_STATES = "bad_states"
CONF_COOLDOWN = "cooldown"
CONF_STALENESS_THRESHOLD = "staleness_threshold"
CONF_BATTERY_THRESHOLD = "battery_threshold"
CONF_BATTERY_ENTITY_MAP = "battery_entity_map"
CONF_AVAILABILITY_WINDOWS = "availability_windows"
CONF_USE_DEVICE_NAMES = "use_device_names"

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
DEFAULT_BATTERY_THRESHOLD = 20  # percent
DEFAULT_AVAILABILITY_WINDOWS = ["today", "7d"]
DEFAULT_USE_DEVICE_NAMES = False
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
