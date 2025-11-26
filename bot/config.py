import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1].parent
load_dotenv(BASE_DIR / ".env")

# Load environment variables from a .env file if present (for development)
load_dotenv(override=True)

# --- Configuration ---
# Telegram bot token (required for bot operation)
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is not set.")

# Group chat ID for notifications or group operations (required)
GROUP_CHAT_ID = os.getenv('GROUP_CHAT_ID')
if not GROUP_CHAT_ID:
    raise RuntimeError("GROUP_CHAT_ID environment variable is not set.")
GROUP_CHAT_ID = int(GROUP_CHAT_ID)

# Main group chat for high-quantity orders (required)
MAIN_GROUP_CHAT_ID = os.getenv('MAIN_GROUP_CHAT_ID')
if not MAIN_GROUP_CHAT_ID:
    raise RuntimeError("MAIN_GROUP_CHAT_ID environment variable is not set.")
MAIN_GROUP_CHAT_ID = int(MAIN_GROUP_CHAT_ID)

# Long distance delivery group chat ID (required)
CRG_GROUP_CHAT_ID = os.getenv('CRG_GROUP_CHAT_ID')
if not CRG_GROUP_CHAT_ID:
    raise RuntimeError("CRG_GROUP_CHAT_ID environment variable is not set.")
CRG_GROUP_CHAT_ID = int(CRG_GROUP_CHAT_ID)

def _get_env_int(name: str, required: bool = True) -> int | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        if required:
            raise RuntimeError(f"{name} environment variable is not set.")
        return None
    return int(raw.strip())

# 🔥 вот тут реально читаем твой PROFIT_REPORT_CHAT_ID из .env
PROFIT_REPORT_CHAT_ID = _get_env_int("PROFIT_REPORT_CHAT_ID", required=True)

# Payment information for long distance orders
REVO_INFO = os.getenv('REVO_INFO', 'Please contact admin for Revo payment details')
SOLANA_ADDRESS = os.getenv('SOLANA_ADDRESS', 'Please contact admin for Solana address')

# Feature flags
# Toggle availability of the long distance (other cities) ordering flow
LONG_DISTANCE_ENABLED = os.getenv('LONG_DISTANCE_ENABLED', 'false').lower() in ('1', 'true', 'yes', 'on')

# Channel ID to check for user membership (required)
CHANNEL_ID_TO_CHECK = os.getenv('CHANNEL_ID_TO_CHECK')
if not CHANNEL_ID_TO_CHECK:
    raise RuntimeError("CHANNEL_ID_TO_CHECK environment variable is not set.")
CHANNEL_ID_TO_CHECK = int(CHANNEL_ID_TO_CHECK)
SUBSCRIBE_LINK = os.getenv("SUBSCRIBE_LINK", "https://t.me/Riga_night")

# Admin IDs (list of user IDs of your administrators, comma-separated in .env, required)
ADMIN_IDS_RAW = os.getenv('ADMIN_IDS')
if not ADMIN_IDS_RAW:
    raise RuntimeError("ADMIN_IDS environment variable is not set.")
ADMIN_IDS = [int(x) for x in ADMIN_IDS_RAW.split(',') if x.strip()]

# Exception user IDs for order limit (optional, comma-separated in .env)
ORDER_LIMIT_EXCEPTION_USER_IDS_RAW = os.getenv('ORDER_LIMIT_EXCEPTION_USER_IDS', '')
ORDER_LIMIT_EXCEPTION_USER_IDS = [int(x) for x in ORDER_LIMIT_EXCEPTION_USER_IDS_RAW.split(',') if x.strip()]

# Primary admin for order cancellation approval
PRIMARY_ADMIN_ID_RAW = os.getenv('PRIMARY_ADMIN_ID')
print(f"[DEBUG] PRIMARY_ADMIN_ID_RAW from environment: {PRIMARY_ADMIN_ID_RAW}")
if not PRIMARY_ADMIN_ID_RAW:
    raise RuntimeError("PRIMARY_ADMIN_ID environment variable is not set.")
PRIMARY_ADMIN_ID = int(PRIMARY_ADMIN_ID_RAW)
print(f"[DEBUG] PRIMARY_ADMIN_ID converted to int: {PRIMARY_ADMIN_ID}")

# Data file paths (relative to project root)
# Base directory for all data files
DATA_DIR = Path(os.getenv('DATA_DIR', 'data'))
# Path to order counts data file
DATA_FILE = DATA_DIR / "order_counts.json"
# Path to courier data file
COURIER_DATA_FILE = DATA_DIR / "courier_data.json"
# Path to loyalty data file
LOYALTY_DATA_FILE = DATA_DIR / "loyalty_data.json"
# Directory for order files
ORDERS_DIR = DATA_DIR / "orders"

# --- Loyalty System Configuration ---
# Number of orders per loyalty cycle
LOYALTY_CYCLE_SIZE = 10  # Number of orders per cycle
# Percentage bonus for loyalty
LOYALTY_PERCENTAGE = 10  # Percentage bonus (10%)
# Name for the loyalty bonus item
LOYALTY_BONUS_NAME = "🥦 Loyalty Bonus"  # Name for the bonus item

# Loyalty bonus exceptions (users who should not benefit from loyalty bonuses)
LOYALTY_EXCEPTION_USER_IDS_RAW = os.getenv('LOYALTY_EXCEPTION_USER_IDS', '')
LOYALTY_EXCEPTION_USER_IDS = [int(x) for x in LOYALTY_EXCEPTION_USER_IDS_RAW.split(',') if x.strip()]

# --- Late Delivery Notifier Configuration ---
LATE_DELIVERY_CHECK_INTERVAL_MINUTES = int(os.getenv('LATE_DELIVERY_CHECK_INTERVAL_MINUTES', '1'))  # Default 1 minute

# --- Large Order Balance Configuration ---
# Maximum difference in order counts between couriers for large orders (5+ quantity)
# This prevents one courier from having too many more orders than others
MAX_LARGE_ORDER_COUNT_DIFFERENCE = int(os.getenv('MAX_LARGE_ORDER_COUNT_DIFFERENCE', '2'))  # Default 2 orders difference
