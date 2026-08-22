import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "bot_database.db")

# ===== TELEGRAM =====
TOKEN = os.getenv("TOKEN")

# ===== INTERVALS =====
DEFAULT_INTERVAL = 60
DEFAULT_MARKET_INTERVAL = 30

ALLOWED_INTERVALS = (30, 60, 120, 240, 400)
ALLOWED_MARKET_INTERVALS = (5, 10, 20, 30, 60)

# ===== WEATHER UNDERGROUND =====
WU_API_KEYS = [
    os.getenv("WU_API_KEY_1"),
    os.getenv("WU_API_KEY_2"),
]

# ===== POLYMARKET =====
POLY_PRIVATE_KEY      = os.getenv("POLY_PRIVATE_KEY")
POLY_API_KEY          = os.getenv("POLY_API_KEY")
POLY_API_SECRET       = os.getenv("POLY_API_SECRET")
POLY_API_PASSPHRASE   = os.getenv("POLY_API_PASSPHRASE")
POLY_FUNDER           = os.getenv("POLY_FUNDER")
POLY_SIGNATURE_TYPE   = int(os.getenv("POLY_SIGNATURE_TYPE", "1"))