"""Constants for the PyTap integration."""

DOMAIN = "pytap"
DEFAULT_NAME = "PyTap"

# Configuration keys
CONF_HOST = "host"
CONF_PORT = "port"
CONF_MODULES = "modules"
CONF_LIVE_REPORTING = "live_reporting"
CONF_WRITE_INTERVAL = "write_interval"
# Optional per-entry label for the inverter (or array) this gateway serves.
# Used as the first part of device names; blank for single-gateway installs.
CONF_INVERTER = "inverter"

# Module dict keys
CONF_MODULE_STRING = "string"
CONF_MODULE_NAME = "name"
CONF_MODULE_BARCODE = "barcode"
CONF_MODULE_PEAK_POWER = "peak_power"

# Defaults
DEFAULT_PORT = 502

# Transport (adapted from Tim Laing's upstream PR azebro/pytap#36): a TCP
# converter, a local serial adapter, or a Home Assistant ESPHome serial proxy.
CONF_CONNECTION_TYPE = "connection_type"
CONF_SERIAL_PORT = "serial_port"
CONF_BAUD_RATE = "baud_rate"
CONNECTION_TCP = "tcp"
CONNECTION_SERIAL = "serial"
DEFAULT_BAUD_RATE = 38400
CONNECTION_TIMEOUT = 10
SERIAL_READ_TIMEOUT = 1
DEFAULT_STRING_NAME = "Default"
DEFAULT_PEAK_POWER = 455
DEFAULT_SCAN_INTERVAL = 30
DEFAULT_LIVE_REPORTING = True
DEFAULT_WRITE_INTERVAL = 60
DEFAULT_INVERTER = ""
CONF_ESTIMATE_SILENT = "estimate_silent"

# What a quiet optimizer's own live sensors show (adapted from Tim Laing's
# upstream PR azebro/pytap#34, fitted to the fork's sun-aware rules).
CONF_NIGHT_ACTION = "night_action"  # asleep: "zero" or "hold"
CONF_SILENT_ACTION = "silent_action"  # silent by day: "unavailable", "hold", "zero"
CONF_SILENT_MINUTES = "silent_minutes"

# Passive module discovery (adapted from Tim Laing's upstream PR azebro/pytap#37).
CONF_AUTO_DISCOVERY = "auto_discovery"
CONF_EXCLUDED_BARCODES = "excluded_barcodes"
DEFAULT_DISCOVERED_STRING = "Discovered"

# Optional inverter per string ({string: inverter}), for one CCA serving
# several inverters; strings without one use the entry's inverter name.
CONF_STRING_INVERTERS = "string_inverters"
STALE_ZERO = "zero"
STALE_HOLD = "hold"
STALE_UNAVAILABLE = "unavailable"
NIGHT_ACTIONS = (STALE_ZERO, STALE_HOLD)
SILENT_ACTIONS = (STALE_UNAVAILABLE, STALE_HOLD, STALE_ZERO)
DEFAULT_NIGHT_ACTION = STALE_ZERO
DEFAULT_SILENT_ACTION = STALE_UNAVAILABLE
MIN_SILENT_MINUTES = 5
MAX_SILENT_MINUTES = 240
DEFAULT_ESTIMATE_SILENT = True
MIN_WRITE_INTERVAL = 5
MAX_WRITE_INTERVAL = 300
RECONNECT_TIMEOUT = 60
RECONNECT_DELAY = 5
RECONNECT_MAX_DELAY = 60
RECONNECT_RETRIES = 0

# Energy accumulation tuning
ENERGY_GAP_THRESHOLD_SECONDS = 120
ENERGY_LOW_POWER_THRESHOLD_W = 1.0

# Node mapping status (diagnostic sensor)
MAPPING_WAITING = "waiting"
MAPPING_PARTIAL = "partial"
MAPPING_COMPLETE = "complete"

# Reading health (see coordinator.reading_state)
# Optimizers stop reporting at night, and by day they can drop off the radio
# link for a while (2.4 GHz interference), so silence alone means little.
# Below LOW_SUN_ELEVATION a silent optimizer is treated as asleep: live
# readings go to 0. Above it, a silent optimizer keeps its last value until
# SILENT_UNAVAILABLE_SECONDS have passed, then its live readings become
# unavailable. Energy sensors are never affected.
HEALTH_CHECK_INTERVAL_SECONDS = 60
LOW_SUN_ELEVATION = 10.0
NIGHT_QUIET_SECONDS = 10 * 60
SILENT_UNAVAILABLE_SECONDS = 30 * 60
DEFAULT_SILENT_MINUTES = SILENT_UNAVAILABLE_SECONDS // 60
# No bytes at all from the converter for this long: live readings unavailable.
LINK_DOWN_SECONDS = 5 * 60
# Pending node table: how often to repeat the INFO reminder.
PENDING_LOG_INTERVAL_SECONDS = 10 * 60
# RS-485 frame error rate that triggers a wiring warning.
BUS_ERROR_WINDOW_SECONDS = 10 * 60
BUS_ERROR_MIN_FRAMES = 500
BUS_ERROR_RATE_WARN = 0.02
BUS_ERROR_LOG_INTERVAL_SECONDS = 60 * 60

# Estimating silent optimizers (string and total sensors only)
# Optimizers in a string carry the same current, so while most of a
# string's optimizers report normally a silent one is almost certainly
# producing too. Its power is estimated as its learned share of the
# string's average, and the estimate is added to string/total energy.
ESTIMATE_AFTER_SECONDS = ENERGY_GAP_THRESHOLD_SECONDS
ESTIMATE_MIN_PEER_FRACTION = 0.5
# After a Home Assistant restart, an optimizer's first reading is compared
# with its last one before the stop. Gaps up to this long are bridged with
# the average of the two readings, counted as estimated energy.
RESTART_BRIDGE_MAX_SECONDS = 600
SHARE_MIN_PEER_POWER_W = 10.0
SHARE_SMOOTHING = 0.2
SHARE_MAX = 2.0

# Data validity sensor states (per optimizer)
VALIDITY_REPORTING = "reporting"
VALIDITY_HOLDING = "holding"
VALIDITY_ESTIMATED = "estimated"
VALIDITY_SILENT_ESTIMATED = "silent_estimated"
VALIDITY_ASLEEP = "asleep"
VALIDITY_SILENT = "silent"
VALIDITY_STATES = [
    VALIDITY_REPORTING,
    VALIDITY_HOLDING,
    VALIDITY_ESTIMATED,
    VALIDITY_SILENT_ESTIMATED,
    VALIDITY_ASLEEP,
    VALIDITY_SILENT,
]

# CCA bus status (per gateway / config entry). When it isn't normal, every
# optimizer's live readings are unavailable.
BUS_NORMAL = "normal"
BUS_CONVERTER_UNREACHABLE = "converter_unreachable"
BUS_QUIET = "bus_quiet"
BUS_STATES = [BUS_NORMAL, BUS_CONVERTER_UNREACHABLE, BUS_QUIET]

# Per-optimizer reading states
READING_OK = "ok"
READING_ASLEEP = "asleep"
READING_SILENT = "silent"
READING_OFFLINE = "offline"
