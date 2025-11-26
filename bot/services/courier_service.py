from bot.utils.data import save_courier_data, get_today_key

def ensure_courier_initialized(courier_data, courier_id, courier_username="Unknown", courier_full_name="Unknown"):
    if courier_id not in courier_data:
        courier_data[courier_id] = {}

    courier = courier_data[courier_id]

    # Обновляем имя и юзернейм только если они переданы
    if courier_username:
        courier["username"] = courier_username
    if courier_full_name:
        courier["full_name"] = courier_full_name

    courier.setdefault("username", "Unknown")
    courier.setdefault("full_name", "Unknown")
    courier.setdefault("accepted_orders", {})
    courier.setdefault("delivered_orders", {})
    courier.setdefault("brokoli_delivered", {})
    courier.setdefault("cancelled_orders", {})
    courier.setdefault("cancelled_customer_order_numbers", {})

    return courier


def update_courier_accepted_orders(courier_data, courier_id, courier_username, courier_full_name):
    today_key = get_today_key()
    courier = ensure_courier_initialized(courier_data, courier_id, courier_username, courier_full_name)

    courier["accepted_orders"].setdefault(today_key, 0)
    courier["accepted_orders"][today_key] += 1

    courier["brokoli_delivered"].setdefault(today_key, 0)

    save_courier_data(courier_data)


def update_courier_delivered_orders(courier_data, courier_id, courier_username, courier_full_name, quantity=0, loyalty_bonus=0):
    today_key = get_today_key()
    courier = ensure_courier_initialized(courier_data, courier_id, courier_username, courier_full_name)

    courier["delivered_orders"].setdefault(today_key, 0)
    courier["brokoli_delivered"].setdefault(today_key, 0)

    courier["delivered_orders"][today_key] += 1
    courier["brokoli_delivered"][today_key] += quantity + loyalty_bonus

    if courier["accepted_orders"].get(today_key, 0) > 0:
        courier["accepted_orders"][today_key] -= 1

    save_courier_data(courier_data)


def update_courier_cancelled_orders(courier_data, courier_id, courier_username, courier_full_name, customer_order_no):
    today_key = get_today_key()
    courier = ensure_courier_initialized(courier_data, courier_id, courier_username, courier_full_name)

    courier["cancelled_orders"].setdefault(today_key, 0)
    courier["cancelled_orders"][today_key] += 1

    courier["cancelled_customer_order_numbers"].setdefault(today_key, [])
    if customer_order_no not in courier["cancelled_customer_order_numbers"][today_key]:
        courier["cancelled_customer_order_numbers"][today_key].append(customer_order_no)

    courier["brokoli_delivered"].setdefault(today_key, 0)

    save_courier_data(courier_data)
