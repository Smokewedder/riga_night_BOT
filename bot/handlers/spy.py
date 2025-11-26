# bot/handlers/spy.py
import asyncio
import json
import logging
from telegram.constants import ParseMode
# Импорты для работы с файлами
from pathlib import Path 

from bot.config import ADMIN_IDS, PRIMARY_ADMIN_ID

logger = logging.getLogger(__name__)

# Файл для сохранения статуса Spy Mode (в папке data/)
SPY_STATUS_FILE = "data/spy_status.json" 

# --- Управление статусом (ОТСУТСТВОВАЛ У ВАС, ВЫЗЫВАЯ ОШИБКУ) ---

def get_spy_status() -> bool:
    """Проверить, включен ли Spy Mode (загрузка из файла)."""
    try:
        with open(SPY_STATUS_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("enabled", False)
    except FileNotFoundError:
        # Создаем файл, если его нет
        set_spy_status(False)
        return False
    except Exception as e:
        logger.error(f"Error loading spy status: {e}")
        return False

def set_spy_status(is_enabled: bool) -> None:
    """Сохранить статус Spy Mode в файл."""
    try:
        # Создаем папку data/, если ее нет
        Path("data").mkdir(exist_ok=True)
        
        with open(SPY_STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump({"enabled": is_enabled}, f)
    except Exception as e:
        logger.error(f"Error saving spy status: {e}")


# --- Основная функция уведомлений ---

async def notify_admins_order_status(
    context,
    display_no: int,
    order_data: dict,
    action: str,
    actor_text: str = "",
):
    """
    Отправляет обновления статуса заказа всем администраторам (Spy Mode).
    """

    # 1. ПРОВЕРКА СТАТУСА
    if not get_spy_status():
        return

    # 2. ФИЛЬТРАЦИЯ: Игнорируем CREATED, как вы просили
    if action == "CREATED":
        return

    # Define human-readable actions
    action_names = {
        "accepted": "✅ Заказ принят",
        "delivered": "📦 Заказ доставлен",
        "denied": "❌ Заказ отклонен",
        "courier_cancelled": "🚫 Отменен курьером",
    }

    action_emoji = action_names.get(action, f"ℹ️ {action}")
    actor_line = f"👤 <b>{actor_text}</b>" if actor_text else ""

    total_price = order_data.get("total_price", "-")
    payment = order_data.get("payment", "-")
    region = order_data.get("region", "-")
    # Берем имя пользователя из поля 'from'
    username = (
        order_data.get("from")
        or order_data.get("username")
        or "Клиент не указан"
    )
    items = order_data.get("items", [])
    # Используем 'qty' и 'sum' (как в вашем order_1.json), а не 'quantity' и 'subtotal'
    items_preview = "\n".join(
        [f"• {it.get('name', '?')} x{it.get('qty', 1)} — {it.get('sum', 0)}€" for it in items] 
    )

    # Build message
    text = (
        f"{action_emoji}\n"
        f"📦 <b>Заказ #{display_no}</b>\n"
        f"{actor_line}\n\n"
        f"👤 Клиент: {username}\n"
        f"📍 Регион: {region}\n"
        f"💳 Оплата: {payment}\n"
        f"💵 Итого: {total_price}€\n\n"
        f"🍹 Состав заказа:\n{items_preview}"
    )

    # Send to all admins
    admin_targets = set(ADMIN_IDS or []) | {PRIMARY_ADMIN_ID}
    tasks = []

    for admin_id in admin_targets:
        if admin_id:
            tasks.append(
                context.bot.send_message(
                    chat_id=admin_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                )
            )

    # Запускаем все задачи одновременно
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)