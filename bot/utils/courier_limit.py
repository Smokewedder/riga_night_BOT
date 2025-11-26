import json
import os
from bot.utils.data import load_large_order_counts
from bot.config import MAX_LARGE_ORDER_COUNT_DIFFERENCE

LIMIT_FILE = "data/courier_limit.json"
DEFAULT_LIMIT = 4
INACTIVE_COURIERS_FILE = "data/inactive_couriers.json"
BALANCE_LIMIT_FILE = "data/balance_limit.json"

def get_courier_order_limit():
    if not os.path.exists(LIMIT_FILE):
        return DEFAULT_LIMIT
    with open(LIMIT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("max_active_orders_per_courier", DEFAULT_LIMIT)

def set_courier_order_limit(new_limit):
    with open(LIMIT_FILE, "w", encoding="utf-8") as f:
        json.dump({"max_active_orders_per_courier": new_limit}, f, ensure_ascii=False, indent=2)

def load_inactive_couriers():
    """Load list of inactive couriers for the current week"""
    if not os.path.exists(INACTIVE_COURIERS_FILE):
        return []
    with open(INACTIVE_COURIERS_FILE, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
            return data.get("inactive_couriers", [])
        except:
            return []

def save_inactive_couriers(inactive_couriers):
    """Save list of inactive couriers"""
    with open(INACTIVE_COURIERS_FILE, "w", encoding="utf-8") as f:
        json.dump({"inactive_couriers": inactive_couriers}, f, ensure_ascii=False, indent=2)

def add_inactive_courier(courier_id: int):
    """Add a courier to the inactive list"""
    inactive = load_inactive_couriers()
    courier_id_str = str(courier_id)
    if courier_id_str not in inactive:
        inactive.append(courier_id_str)
        save_inactive_couriers(inactive)

def remove_inactive_courier(courier_id: int):
    """Remove a courier from the inactive list"""
    inactive = load_inactive_couriers()
    courier_id_str = str(courier_id)
    if courier_id_str in inactive:
        inactive.remove(courier_id_str)
        save_inactive_couriers(inactive)

def can_courier_accept_large_order(courier_id: int) -> bool:
    """
    Check if a courier can accept a large order (5+ quantity) based on the order count difference limit.
    
    Args:
        courier_id: The ID of the courier trying to accept the order
        
    Returns:
        bool: True if the courier can accept the order, False otherwise
    """
    from bot.config import PRIMARY_ADMIN_ID
    
    # Load current large order counts for the week
    large_counts_data = load_large_order_counts()
    counts = large_counts_data.get("counts", {})
    
    # If no couriers have any orders yet, anyone can accept
    if not counts:
        return True
    
    # Load inactive couriers and filter them out
    inactive_couriers = load_inactive_couriers()
    active_counts = {}
    for courier_key, info in counts.items():
        # Skip inactive couriers AND primary admin
        if courier_key not in inactive_couriers and int(courier_key) != PRIMARY_ADMIN_ID:
            active_counts[courier_key] = info
    
    # If no active couriers have orders, anyone can accept
    if not active_counts:
        return True
    
    # Get current courier's order count
    courier_key = str(courier_id)
    current_courier_count = active_counts.get(courier_key, {}).get("count", 0)
    
    # Calculate what the new counts would be if this courier accepts
    new_courier_count = current_courier_count + 1
    
    # Create a new list with the updated count for this courier
    new_order_counts = []
    for cid, info in active_counts.items():
        if cid == courier_key:
            new_order_counts.append(new_courier_count)
        else:
            new_order_counts.append(info.get("count", 0))
    
    # Calculate new min and max
    new_min_count = min(new_order_counts)
    new_max_count = max(new_order_counts)
    new_difference = new_max_count - new_min_count
    
    # Get the current balance limit dynamically
    current_limit = get_balance_limit()
    
    # Check if the new difference would exceed the limit
    if new_difference > current_limit:
        return False
    
    return True

def get_large_order_balance_info() -> dict:
    """
    Get information about the current large order balance between couriers.
    
    Returns:
        dict: Information about current order counts and balance status
    """
    from bot.config import PRIMARY_ADMIN_ID
    
    large_counts_data = load_large_order_counts()
    counts = large_counts_data.get("counts", {})
    
    # Load inactive couriers and filter them out
    inactive_couriers = load_inactive_couriers()
    active_counts = {}
    for courier_key, info in counts.items():
        # Skip inactive couriers AND primary admin
        if courier_key not in inactive_couriers and int(courier_key) != PRIMARY_ADMIN_ID:
            active_counts[courier_key] = info
    
    if not active_counts:
        current_limit = get_balance_limit()
        return {
            "has_orders": False,
            "min_count": 0,
            "max_count": 0,
            "difference": 0,
            "limit": current_limit,
            "inactive_couriers": inactive_couriers
        }
    
    order_counts = [info.get("count", 0) for info in active_counts.values()]
    min_count = min(order_counts)
    max_count = max(order_counts)
    difference = max_count - min_count
    
    current_limit = get_balance_limit()
    return {
        "has_orders": True,
        "min_count": min_count,
        "max_count": max_count,
        "difference": difference,
        "limit": current_limit,
        "is_balanced": difference <= current_limit,
        "inactive_couriers": inactive_couriers,
        "active_couriers": list(active_counts.keys())
    }

def get_balance_limit():
    """Get the current balance limit from JSON file"""
    if not os.path.exists(BALANCE_LIMIT_FILE):
        # Initialize with default value from config
        set_balance_limit(MAX_LARGE_ORDER_COUNT_DIFFERENCE)
        return MAX_LARGE_ORDER_COUNT_DIFFERENCE
    
    with open(BALANCE_LIMIT_FILE, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
            return data.get("max_large_order_count_difference", MAX_LARGE_ORDER_COUNT_DIFFERENCE)
        except:
            return MAX_LARGE_ORDER_COUNT_DIFFERENCE

def set_balance_limit(new_limit):
    """Set the balance limit in JSON file for dynamic updates"""
    os.makedirs("data", exist_ok=True)
    with open(BALANCE_LIMIT_FILE, "w", encoding="utf-8") as f:
        json.dump({"max_large_order_count_difference": new_limit}, f, ensure_ascii=False, indent=2)
