"""Settings for the high-temperature AI. Everything you might want to tune lives here."""
import os

# --- Kalshi ---------------------------------------------------------------------
# Public market data needs no API key. The client tries these hosts in order.
KALSHI_BASES = [
    "https://api.elections.kalshi.com/trade-api/v2",
    "https://external-api.kalshi.com/trade-api/v2",
]

# --- Cities -----------------------------------------------------------------------
# `setup` discovers every daily-high series on Kalshi and reads the settlement station
# from each series' own rules (titles can be wrong). This table is the fallback and
# supplies coordinates. Keys are series tickers.
# (city, NWS station, lat, lon, IANA time zone)
BUILTIN_CITIES = {
    "KXHIGHNY":    ("New York (Central Park)", "KNYC", 40.7789, -73.9692, "America/New_York"),
    "KXHIGHCHI":   ("Chicago (Midway)",        "KMDW", 41.7860, -87.7524, "America/Chicago"),
    "KXHIGHMIA":   ("Miami",                   "KMIA", 25.7881, -80.3169, "America/New_York"),
    "KXHIGHAUS":   ("Austin",                  "KAUS", 30.1831, -97.6799, "America/Chicago"),
    "KXHIGHLAX":   ("Los Angeles (LAX)",       "KLAX", 33.9382, -118.3866, "America/Los_Angeles"),
    "KXHIGHDEN":   ("Denver",                  "KDEN", 39.8466, -104.6562, "America/Denver"),
    "KXHIGHPHIL":  ("Philadelphia",            "KPHL", 39.8733, -75.2268, "America/New_York"),
    "KXHIGHTPHX":  ("Phoenix",                 "KPHX", 33.4277, -112.0038, "America/Phoenix"),
    "KXHIGHTHOU":  ("Houston (Hobby)",         "KHOU", 29.6375, -95.2825, "America/Chicago"),
    "KXHIGHTMIN":  ("Minneapolis",             "KMSP", 44.8831, -93.2289, "America/Chicago"),
    "KXHIGHTOKC":  ("Oklahoma City",           "KOKC", 35.3889, -97.6006, "America/Chicago"),
    "KXHIGHTSFO":  ("San Francisco (SFO)",     "KSFO", 37.6197, -122.3647, "America/Los_Angeles"),
    "KXHIGHTDC":   ("Washington DC (DCA)",     "KDCA", 38.8483, -77.0342, "America/New_York"),
    "KXHIGHTBOS":  ("Boston",                  "KBOS", 42.3606, -71.0097, "America/New_York"),
    "KXHIGHTDAL":  ("Dallas (DFW)",            "KDFW", 32.8975, -97.0219, "America/Chicago"),
    "KXHIGHTSEA":  ("Seattle",                 "KSEA", 47.4447, -122.3144, "America/Los_Angeles"),
    "KXHIGHTLV":   ("Las Vegas",               "KLAS", 36.0719, -115.1634, "America/Los_Angeles"),
    "KXHIGHTATL":  ("Atlanta",                 "KATL", 33.6301, -84.4418, "America/New_York"),
    "KXHIGHTSATX": ("San Antonio",             "KSAT", 29.5443, -98.4839, "America/Chicago"),
    "KXHIGHTNOLA": ("New Orleans",             "KMSY", 29.9934, -90.2580, "America/Chicago"),
    "KXHIGHTSAN":  ("San Diego",               "KSAN", 32.7336, -117.1831, "America/Los_Angeles"),
    "KXHIGHTSDF":  ("Louisville",              "KSDF", 38.1811, -85.7392, "America/Kentucky/Louisville"),
    "KXHIGHTEWR":  ("Newark",                  "KEWR", 40.6825, -74.1694, "America/New_York"),
    "KXHIGHTTTN":  ("Trenton",                 "KTTN", 40.2767, -74.8135, "America/New_York"),
}

# --- Weather data -------------------------------------------------------------------
# Open-Meteo Previous Runs API: each model's forecast as it was issued N days before
# the target hour, so the backtest only sees what existed at decision time.
FORECAST_MODELS = ["gfs_seamless", "ecmwf_ifs025", "icon_seamless", "gem_seamless"]
LEADS = [1, 2]
WEATHER_START = "2024-01-15"   # the forecast archive generally starts in January 2024

# --- Market data ---------------------------------------------------------------------
MARKET_HISTORY_DAYS = 240      # how far back `setup` pulls Kalshi prices (more = slower first run)
DECISION_HOUR_LOCAL = 8        # prices and forecasts are taken at 08:00 local time on the day

# --- Picks ------------------------------------------------------------------------------
EDGE_THRESHOLD = 0.04          # min expected profit per $1 contract after fees
MAX_SPREAD = 0.10              # ignore brackets with a wider bid/ask spread
MIN_PRICE, MAX_PRICE = 0.04, 0.96   # skip near-certain contracts: tail errors dominate there
MAX_PICKS_PER_CITY = 2         # brackets in one city are correlated
BASE_FEE_RATE = 0.07           # taker fee = rate x series fee_multiplier x C x P x (1-P), rounded up
KELLY_FRACTION = 0.25          # suggested stake = this fraction of the Kelly bet...
MAX_STAKE_PCT = 0.02           # ...capped at this share of bankroll

# --- Run time budget ----------------------------------------------------------------
# Long downloads stop after this many minutes so the run can save its progress; the next
# run continues where this one stopped. Unset = no limit (local runs).
TIME_BUDGET_MIN = float(os.environ.get("TIME_BUDGET_MIN", "0") or 0)

# Bump when the model's inputs change, so the backtest re-runs on the next daily update.
FEATURES_VERSION = 3   # 2 = adds the NWS station forecast (NBM); 3 = fixes which NBM value is the high

# --- Backtest ------------------------------------------------------------------------
MIN_TRAIN_DAYS = 120
MIN_BLEND_DAYS = 45

DATA_DIR = "data"
RESULTS_DIR = "results"
DASHBOARD_PATH = "dashboard.html"

# --- Website ---------------------------------------------------------------------
SITE_NAME = "Lucas Lin Weather"
# The site's public address. On GitHub the workflow fills this in automatically
# (e.g. https://yourname.github.io/lucaslinweather). Set SITE_DOMAIN only if you buy a
# custom domain such as "lucaslinweather.com".
SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")
SITE_DOMAIN = os.environ.get("SITE_DOMAIN", "")
SITE_DIR = "site"
HUB_URL = "/"   # front page linking all bots (on github.io); set "" to hide the link
