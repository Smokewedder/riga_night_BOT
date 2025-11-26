import json
from pathlib import Path
from datetime import date, timedelta
from typing import Dict, Any

# Expected in bot.config
from bot.config import DATA_FILE, COURIER_DATA_FILE

DRINKS_FILE = Path("data/drinks.json")
# Local data files
ORDER_INTAKE_STATUS_FILE = Path("data/order_intake_status.json")
USER_IDS_FILE = Path("data/user_ids.json")
USER_MESSAGES_FILE = Path("data/user_messages.json")


def get_today_key() -> str:
    return date.today().isoformat()


def load_data(file_path: Path, default_data=None):
    """
    Loads JSON data from a file, with optional default data.
    """
    if file_path.exists():
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return default_data if default_data is not None else {}
    return default_data if default_data is not None else {}

def load_drinks():
    """Loads drinks menu from drinks.json (supports both old and new formats)."""
    drinks_path = Path(__file__).resolve().parent.parent.parent / "data" / "drinks.json"
    if drinks_path.exists():
        try:
            with open(drinks_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # ✅ New structure (dict with categories as keys)
            if isinstance(data, dict) and "categories" not in data:
                return data

            # 🕐 Old structure (with categories)
            if "categories" in data:
                formatted = {}
                for cat in data.get("categories", []):
                    cid = cat.get("id")
                    cname = cat.get("name", {})
                    formatted[cid] = {
                        "name": cname,
                        "items": {
                            d.get("id"): {
                                "ru": d["name"]["ru"],
                                "en": d["name"]["en"],
                                "lv": d["name"]["lv"],
                                "price": d.get("price", 0)
                            }
                            for d in cat.get("drinks", [])
                        },
                    }
                return formatted

        except json.JSONDecodeError as e:
            print(f"[ERROR] Failed to parse drinks.json: {e}")
    else:
        print(f"[WARN] drinks.json not found at {drinks_path}")

    return {}


def save_data(file_path: Path, data: Any):
    """
    Saves data to a JSON file.
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def save_order_counts(user_order_data: Dict[str, Any]):
    """
    Saves general user order counts (if needed for analytics).
    """
    save_data(DATA_FILE, user_order_data)


def save_courier_data(courier_data: Dict[str, Any]):
    """
    Saves courier data.
    """
    save_data(COURIER_DATA_FILE, courier_data)


def get_order_path_by_number(order_number: int, fallback_today=True, search_days=3) -> Path | None:
    """
    Finds an order JSON file by its number within the last 'search_days'.
    Assumes orders are stored in: data/orders/YYYY-MM-DD/order_<number>.json
    """
    orders_dir = Path("data/orders")
    for days_ago in range(search_days):
        day = date.today() - timedelta(days=days_ago)
        day_str = day.isoformat()
        order_path = orders_dir / day_str / f"order_{order_number}.json"
        if order_path.exists():
            try:
                with open(order_path, "r", encoding="utf-8") as f:
                    order_data = json.load(f)
                order_date = order_data.get("date")
                if order_date:
                    correct_path = orders_dir / order_date / f"order_{order_number}.json"
                    return correct_path if correct_path.exists() else order_path
                else:
                    return order_path
            except Exception:
                continue

    if fallback_today:
        today_str = date.today().isoformat()
        fallback_path = orders_dir / today_str / f"order_{order_number}.json"
        if fallback_path.exists():
            return fallback_path

    return None


def get_order_intake_status() -> bool:
    """
    Returns True if order intake is enabled, False otherwise.
    Default is True.
    """
    data = load_data(ORDER_INTAKE_STATUS_FILE, default_data={"enabled": True})
    return data.get("enabled", True)


def set_order_intake_status(enabled: bool):
    """
    Sets the order intake status flag.
    """
    save_data(ORDER_INTAKE_STATUS_FILE, {"enabled": enabled})


def load_user_ids() -> Dict[str, str | None]:
    """
    Loads user IDs and usernames from user_ids.json as a dictionary.
    Format: { "12345678": "@username", ... }
    """
    USER_IDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if USER_IDS_FILE.exists():
        with open(USER_IDS_FILE, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                if isinstance(data, list):
                    return {str(uid): None for uid in data}
                return {str(k): v for k, v in data.items()}
            except json.JSONDecodeError:
                return {}
    return {}


def save_user_ids(user_dict: Dict[str, str | None]):
    """
    Saves user IDs and usernames to user_ids.json.
    """
    if not user_dict:
        return
    USER_IDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(USER_IDS_FILE, "w", encoding="utf-8") as f:
        json.dump(dict(user_dict), f, ensure_ascii=False, indent=2)


def add_or_update_user(user_id: int, username: str | None = None):
    """
    Adds or updates a user in user_ids.json.
    If username is provided, it's saved or updated.
    """
    user_dict = load_user_ids()
    user_id_str = str(user_id)

    if username and username.strip():
        if not username.startswith("@"):
            username = f"@{username}"
        user_dict[user_id_str] = username
    elif user_id_str not in user_dict:
        user_dict[user_id_str] = None

    save_user_ids(user_dict)


def resolve_user_identifier(identifier: str) -> int | None:
    """
    Accepts @username or user_id as string. Returns user_id (int) if found.
    """
    users = load_user_ids()
    identifier = identifier.strip()

    if identifier.isdigit():
        return int(identifier) if identifier in users else None

    if identifier.startswith("@"):
        username = identifier.lower()
        for uid, uname in users.items():
            if uname and uname.lower() == username:
                return int(uid)

    return None

def load_user_messages() -> dict:
    """
    Loads all stored message IDs per user from data/user_messages.json.
    Format: { "123456": [111, 222, 333], ... }
    """
    if USER_MESSAGES_FILE.exists():
        with open(USER_MESSAGES_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}

def save_user_messages(data: dict):
    """
    Saves message IDs per user to data/user_messages.json.
    """
    USER_MESSAGES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(USER_MESSAGES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def add_user_message(user_id: int, message_id: int):
    """
    Adds a new message_id for a given user_id to user_messages.json.
    """
    data = load_user_messages()
    uid = str(user_id)
    if uid not in data:
        data[uid] = []
    if message_id not in data[uid]:
        data[uid].append(message_id)
    save_user_messages(data)

def clear_user_messages(user_id: int):
    """
    Removes all stored messages for a user (used after delivery).
    """
    data = load_user_messages()
    uid = str(user_id)
    if uid in data:
        del data[uid]
        save_user_messages(data)