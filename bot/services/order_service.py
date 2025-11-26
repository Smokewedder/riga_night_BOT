# order_service.py
import json
import random
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

# Base orders directory relative to project root
ORDERS_DIR = Path("data") / "orders"
ORDERS_DIR.mkdir(parents=True, exist_ok=True)


def _today_folder() -> Path:
    """Folder for today's orders (YYYY-MM-DD)."""
    today_key = date.today().isoformat()
    folder = ORDERS_DIR / today_key
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _ensure_folder_for_date(d: date) -> Path:
    folder = ORDERS_DIR / d.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def iter_all_order_files() -> List[Path]:
    """Return list of all order json files (sorted by date folder)."""
    files: List[Path] = []
    if not ORDERS_DIR.exists():
        return files
    for day_dir in sorted(ORDERS_DIR.iterdir()):
        if day_dir.is_dir():
            for f in day_dir.glob("order_*.json"):
                files.append(f)
    return files


def iter_order_files_between(start: date, end: date) -> List[Path]:
    """Collect order files between start and end inclusive."""
    files: List[Path] = []
    cur = start
    while cur <= end:
        folder = ORDERS_DIR / cur.isoformat()
        if folder.exists() and folder.is_dir():
            for p in folder.glob("order_*.json"):
                files.append(p)
        cur = cur + timedelta(days=1)
    return files


def _load_json_safe(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def get_next_display_no(for_date: Optional[date] = None) -> int:
    """
    Return next daily incremental display number for orders.
    If for_date is None => uses today.
    """
    d = for_date or date.today()
    folder = ORDERS_DIR / d.isoformat()
    if not folder.exists():
        return 1
    max_no = 0
    for p in folder.glob("order_*.json"):
        od = _load_json_safe(p)
        if od and isinstance(od.get("display_no"), int):
            if od["display_no"] > max_no:
                max_no = od["display_no"]
        else:
            # fallback: try parse from filename order_<num>.json
            try:
                name = p.stem  # order_<num>
                parts = name.split("_")
                if len(parts) >= 2 and parts[1].isdigit():
                    n = int(parts[1])
                    if n > max_no:
                        max_no = n
            except Exception:
                continue
    return max_no + 1


def generate_random_delivery_no() -> str:
    """Generate a random 5-digit delivery number as a string (may repeat)."""
    n = random.randint(0, 99999)
    return str(n).zfill(5)


def create_order_log(
    display_no: int,
    delivery_no: str,
    user_id: int,
    sender_info: str,
    order_data: Dict[str, Any],
    for_date: Optional[date] = None,
) -> Tuple[Path, Dict[str, Any]]:
    """
    Create and save order JSON file for the given display_no and delivery_no.
    Returns (path, saved_data).
    `order_data` is expected to contain keys: items, total_price, payment, region, location, note, time, etc.
    File path: data/orders/YYYY-MM-DD/order_<display_no>.json
    """
    d = for_date or date.today()
    folder = _ensure_folder_for_date(d)
    folder.mkdir(parents=True, exist_ok=True)

    # Prepare canonical order object
    saved = {
        "display_no": int(display_no),
        "delivery_no": str(delivery_no),
        "user_id": int(user_id),
        "from": sender_info,
        "status": order_data.get("status", "pending"),
        "items": order_data.get("items", []),
        "total_price": order_data.get("total_price", 0),
        "payment": order_data.get("payment", "-"),
        "region": order_data.get("region", "-"),
        "location": order_data.get("location"),
        "location_link": order_data.get("location_link", order_data.get("location_link", "-")),
        "note": order_data.get("note", "-"),
        "time": order_data.get("time", "-"),
        "timestamp": order_data.get("timestamp", datetime.now().isoformat()),
    }

    file_path = folder / f"order_{display_no}.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(saved, f, ensure_ascii=False, indent=2)

    return file_path, saved


def load_order_by_display_no(display_no: int, search_days: int = 7) -> Optional[Tuple[Path, Dict[str, Any]]]:
    """
    Find and load an order by its daily display_no. Searches backward up to `search_days` days including today.
    Returns (path, data) or None.
    """
    for days_ago in range(search_days):
        day = date.today() - timedelta(days=days_ago)
        path = ORDERS_DIR / day.isoformat() / f"order_{display_no}.json"
        if path.exists():
            od = _load_json_safe(path)
            if od:
                return path, od
    return None


def load_orders_by_delivery_no(delivery_no: str, search_days: Optional[int] = None) -> List[Tuple[Path, Dict[str, Any]]]:
    """
    Find all orders with a given delivery_no.
    If search_days is None -> search all orders. Otherwise search last `search_days` days.
    Returns list of (path, data).
    """
    results: List[Tuple[Path, Dict[str, Any]]] = []
    if search_days is None:
        files = iter_all_order_files()
    else:
        start = date.today() - timedelta(days=search_days - 1)
        files = iter_order_files_between(start, date.today())

    for p in files:
        od = _load_json_safe(p)
        if not od:
            continue
        if str(od.get("delivery_no")) == str(delivery_no):
            results.append((p, od))
    return results


def update_order_status_by_path(path: Path, new_status: str, extra_fields: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """
    Update the status (and optionally extra_fields) for the order at given path.
    Returns updated order dict or None on failure.
    """
    od = _load_json_safe(path)
    if not od:
        return None
    od["status"] = new_status
    if extra_fields:
        for k, v in extra_fields.items():
            od[k] = v
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(od, f, ensure_ascii=False, indent=2)
        return od
    except Exception:
        return None


def find_orders_for_delivery_no_shortlist(delivery_no: str, search_days: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Helper to return a short list (display_no + date + path) for UI selection when multiple
    orders share same delivery_no. Each entry: { 'display_no': int, 'date': 'YYYY-MM-DD', 'path': str }
    """
    found = load_orders_by_delivery_no(delivery_no, search_days=search_days)
    shortlist: List[Dict[str, Any]] = []
    for path, od in found:
        # path like data/orders/YYYY-MM-DD/order_<display_no>.json
        try:
            day = path.parent.name
            display = od.get("display_no") or int(path.stem.split("_")[1])
        except Exception:
            day = "unknown"
            display = od.get("display_no", -1)
        shortlist.append({"display_no": int(display), "date": day, "path": str(path)})
    return shortlist


def load_order_file(path: Path) -> Optional[Dict[str, Any]]:
    """Public alias to load an order file dict."""
    return _load_json_safe(path)


def update_order_status_by_display_no(display_no: int, new_status: str, search_days: int = 7, extra_fields: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """
    Find an order by display_no (searching recent days) and update its status.
    Returns updated order dict or None.
    """
    found = load_order_by_display_no(display_no, search_days=search_days)
    if not found:
        return None
    path, od = found
    return update_order_status_by_path(path, new_status, extra_fields)


# Small utility to pretty-print path -> delivery_no mapping (for debugging)
def _all_delivery_no_map() -> Dict[str, List[str]]:
    res: Dict[str, List[str]] = {}
    for p in iter_all_order_files():
        od = _load_json_safe(p)
        if not od:
            continue
        dn = str(od.get("delivery_no", ""))
        res.setdefault(dn, []).append(str(p))
    return res
