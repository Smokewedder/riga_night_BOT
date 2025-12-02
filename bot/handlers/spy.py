import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

from telegram.constants import ParseMode
from telegram.helpers import mention_html

from bot.config import ADMIN_IDS, PRIMARY_ADMIN_ID

logger = logging.getLogger(__name__)

# Папка data и файл статусов per-admin
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
SPY_STATUS_FILE = DATA_DIR / "spy_status.json"


# ----------------------
# File helpers for per-admin spy status
# ----------------------
def _load_spy_file() -> dict:
    """Load the per-admin spy status file. Return dict(admin_id_str -> bool)."""
    try:
        if not SPY_STATUS_FILE.exists():
            SPY_STATUS_FILE.write_text(json.dumps({}), encoding="utf-8")
            return {}
        content = SPY_STATUS_FILE.read_text(encoding="utf-8").strip()
        return json.loads(content) if content else {}
    except Exception as e:
        logger.exception(f"[spy] failed to load spy status file: {e}")
        return {}


def _save_spy_file(data: dict) -> None:
    try:
        SPY_STATUS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.exception(f"[spy] failed to save spy status file: {e}")


def get_spy_status_for_admin(admin_id: int) -> bool:
    """Return True if spy mode is ON for this admin. Default: False."""
    try:
        data = _load_spy_file()
        return bool(data.get(str(admin_id), False))
    except Exception as e:
        logger.exception(f"[spy] get_spy_status_for_admin error: {e}")
        return False


def set_spy_status_for_admin(admin_id: int, enabled: bool) -> None:
    """Set spy mode for single admin."""
    data = _load_spy_file()
    data[str(admin_id)] = bool(enabled)
    _save_spy_file(data)


# ----------------------
# Utilities for building display names / links
# ----------------------
def _resolve_user_display(order: dict) -> str:
    """
    Возвращает HTML-строку для показа клиента:
    - если есть username -> возвращаем @username (telegram автоматически делает его кликабельным);
    - иначе если есть numeric user_id -> возвращаем mention_html(user_id, label),
      это даёт кликабельное имя (на которое можно нажать и перейти в профиль);
    - иначе fallback: plain 'Клиент' или значение поля 'from'.
    """
    username = order.get("username")
    from_field = order.get("from") or order.get("from_display") or ""
    # try common id keys
    user_id = order.get("user_id") or order.get("customer_id") or order.get("sender_id") or order.get("client_id")

    # If username exists -> show @username
    if isinstance(username, str) and username.strip():
        uname = username.lstrip("@").strip()
        return f"@{uname}"

    # If numeric id exists -> use mention_html to create clickable name
    if user_id:
        label = (from_field.strip() if isinstance(from_field, str) and from_field.strip() else "Клиент")
        try:
            return mention_html(int(user_id), label)
        except Exception:
            return label

    # Fallback: if from_field exists (plain name), return it
    if isinstance(from_field, str) and from_field.strip():
        return from_field.strip()

    return "Клиент"


# ----------------------
# Main notify function
# ----------------------
async def notify_admins_order_status(
    context,
    display_no: int,
    order_data: dict,
    action: str,
    actor_text: str = "",
):
    """
    Отправляет обновления статуса заказа администраторам.
    Рассылает только тем админам, у которых включен персональный spy-mode.
    """

    try:
        # Optionally ignore created events
        if action == "CREATED":
            return

        action_names = {
            "accepted": "✅ Заказ принят",
            "delivered": "📦 Заказ доставлен",
            "denied": "❌ Заказ отклонен",
            "courier_cancelled": "🚫 Отменен курьером",
            "pending": "⏳ В ожидании",
            "cancelled": "❎ Отменено",
        }
        action_label = action_names.get(action, f"ℹ️ {action}")

        actor_line = f"👤 <b>{actor_text}</b>\n" if actor_text else ""

        total_price = order_data.get("total_price", "-")
        payment = order_data.get("payment", "-")
        region = order_data.get("region", "-")

        # time: try a few common fields
        order_time = (
            order_data.get("time")
            or order_data.get("created_at")
            or order_data.get("accepted_at")
            or order_data.get("delivered_at")
            or order_data.get("timestamp")
            or order_data.get("date")
            or "-"
        )

        note = order_data.get("note") or order_data.get("comment") or ""

        client_display = _resolve_user_display(order_data)

        # items preview
        items = order_data.get("items", []) or []
        if not isinstance(items, list):
            items = []

        items_preview_lines = []
        for it in items:
            name = it.get("name") or it.get("title") or "?"
            qty = it.get("qty") or it.get("quantity") or 1
            line_sum = it.get("sum") or it.get("subtotal") or it.get("price") or 0
            items_preview_lines.append(f"• {name} x{qty} — {line_sum}€")
        items_preview = "\n".join(items_preview_lines) if items_preview_lines else "—"

        # Build message
        text = (
            f"{action_label}\n"
            f"📦 <b>Заказ #{display_no}</b>\n"
            f"{actor_line}"
            f"👤 Клиент: {client_display}\n"
            f"⏰ Время: {order_time}\n"
            f"📍 Регион: {region}\n"
            f"💳 Оплата: {payment}\n"
            f"💵 Итого: {total_price}€\n"
        )

        if note:
            text += f"\n📝 Примечание: {note}\n"

        text += f"\n🍹 Состав заказа:\n{items_preview}"

        # Collect admin targets
        admin_targets = set()
        if isinstance(ADMIN_IDS, (list, tuple, set)):
            for x in ADMIN_IDS:
                try:
                    if x:
                        admin_targets.add(int(x))
                except Exception:
                    continue
        else:
            # if ADMIN_IDS stored as comma-separated string in config
            if isinstance(ADMIN_IDS, str):
                for part in ADMIN_IDS.split(","):
                    part = part.strip()
                    if part:
                        try:
                            admin_targets.add(int(part))
                        except Exception:
                            pass

        if PRIMARY_ADMIN_ID:
            try:
                admin_targets.add(int(PRIMARY_ADMIN_ID))
            except Exception:
                pass

        # prepare tasks only for admins who have spy ON
        tasks = []
        for admin_id in admin_targets:
            try:
                if get_spy_status_for_admin(admin_id):
                    tasks.append(
                        context.bot.send_message(
                            chat_id=admin_id,
                            text=text,
                            parse_mode=ParseMode.HTML,
                            disable_web_page_preview=True,
                        )
                    )
            except Exception as e:
                logger.exception(f"[spy] failed to schedule send to admin {admin_id}: {e}")

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    logger.warning(f"[spy] send resulted in exception: {r}")

    except Exception as e:
        logger.exception(f"[spy] notify_admins_order_status error: {e}")
