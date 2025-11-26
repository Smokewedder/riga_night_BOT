import json
import logging
from pathlib import Path
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from collections import Counter
from typing import List, Dict, Any, Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
)

from bot.config import ORDERS_DIR, ADMIN_IDS

logger = logging.getLogger(__name__)

# ---------- helpers (ПЕРЕМЕЩЕНЫ ДЛЯ ИСПРАВЛЕНИЯ NameError) ----------

def is_admin(user_id: int) -> bool:
    try:
        return int(user_id) in ADMIN_IDS
    except Exception:
        return False

def money_decimal(value) -> Decimal:
    """Convert any numeric-like value to Decimal(2)."""
    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0.00")
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def fmt_money(d: Decimal) -> str:
    return f"{d:.2f}€"

def format_stat_line(emoji: str, label: str, value) -> str:
    """Вспомогательный хелпер для единообразного форматирования."""
    return f"{emoji} <b>{label}:</b> {value}"

def parse_date(s: str) -> Optional[date]:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def iter_order_files_between(start: date, end: date) -> List[Path]:
    files: List[Path] = []
    current = start
    root = Path(ORDERS_DIR)
    while current <= end:
        folder = root / current.isoformat()
        if folder.exists() and folder.is_dir():
            for p in folder.glob("order_*.json"):
                files.append(p)
        current += timedelta(days=1)
    return files


def iter_all_order_files() -> List[Path]:
    files: List[Path] = []
    root = Path(ORDERS_DIR)
    if not root.exists():
        return files
    for day_dir in sorted(root.iterdir()):
        if day_dir.is_dir():
            for p in day_dir.glob("order_*.json"):
                files.append(p)
    return files


def load_order_file(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("Failed to load order file %s: %s", path, e)
        return None

# --- Себестоимость (ИСПРАВЛЕНО: Добавлен обратный словарь для сопоставления названий) ---

DRINK_COSTS: Dict[str, Decimal] = {}
# Новый словарь: Полное название (из заказа) -> Короткий ключ (для cost)
FULL_NAME_TO_KEY: Dict[str, str] = {} 

def load_drink_costs() -> None:
    """
    Загружает себестоимость напитков ('cost') из drinks.json и строит 
    словарь FULL_NAME_TO_KEY для сопоставления названий из заказов.
    """
    global DRINK_COSTS, FULL_NAME_TO_KEY
    DRINK_COSTS.clear()
    FULL_NAME_TO_KEY.clear()

    # 1. Определяем путь к drinks.json (надежный путь, как в прошлый раз)
    try:
        drinks_path = Path(__file__).resolve().parent.parent.parent / "data" / "drinks.json"
    except Exception:
        drinks_path = Path("data/drinks.json") # Fallback

    if not drinks_path.exists():
        logger.error(f"🚨 drinks.json not found at expected path: {drinks_path.resolve()}. Total Cost = 0.")
        return

    try:
        with open(drinks_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
            for category in data.values():
                for item_key, item in category.get('items', {}).items():
                    name_key = item_key # e.g. "Cēsu Premium"
                    # !!! money_decimal теперь определен выше !!!
                    cost = money_decimal(item.get('cost', 0)) 
                    
                    # 1. Сохраняем себестоимость по короткому ключу
                    DRINK_COSTS[name_key] = cost
                    
                    # 2. Строим обратный словарь: полное_название -> короткий_ключ
                    # Проходим по всем языковым версиям ('ru', 'en', 'lv')
                    for lang in ['ru', 'en', 'lv']:
                        full_name = item.get(lang)
                        if full_name:
                            # Ключ в словаре - полное название из order.json
                            FULL_NAME_TO_KEY[full_name.strip()] = name_key
                            
            if not DRINK_COSTS:
                 logger.warning("drinks.json loaded, but no drink costs were found. Check the 'cost' field structure.")
            else:
                 logger.info(f"Successfully loaded {len(DRINK_COSTS)} drink costs and {len(FULL_NAME_TO_KEY)} name mappings.")
                    
    except Exception as e:
        logger.error(f"🚨 Failed to load drink costs from {drinks_path}: {e}. Error: {e}")

# Вызываем загрузку при старте модуля
load_drink_costs()

def get_item_cost(item_name: str) -> Decimal:
    """
    Возвращает себестоимость напитка. Сначала пытается найти короткий ключ 
    по полному имени из заказа, а затем ищет cost.
    """
    # 1. Ищем короткий ключ по полному названию (из order_*.json)
    short_key = FULL_NAME_TO_KEY.get(item_name.strip())
    
    # 2. Если нашли ключ, ищем cost
    if short_key:
        return DRINK_COSTS.get(short_key, Decimal("0.00"))
        
    # 3. Fallback: если имя из заказа уже является коротким ключом
    return DRINK_COSTS.get(item_name.strip(), Decimal("0.00"))


# ---------- aggregation (логика расчета прибыли верна) ----------


def aggregate_orders(paths: List[Path]) -> Dict[str, Any]:
    """
    Возвращает dict:
      total_orders, gross_revenue (Decimal), net_profit (Decimal),
      items_counter (name -> qty), orders_by_status (status -> count),
      couriers_counter (courier_username -> count), orders_list (raw orders)
    """
    total_orders = 0
    gross_revenue = Decimal("0.00")
    total_cost = Decimal("0.00")
    items_counter: Counter = Counter()
    orders_by_status: Counter = Counter()
    couriers_counter: Counter = Counter()
    orders_list: List[Dict[str, Any]] = []

    for p in paths:
        od = load_order_file(p)
        if not od:
            continue
        total_orders += 1
        orders_list.append(od)

        # status (robust)
        raw_status = od.get("status", "")
        status = str(raw_status).lower()
        orders_by_status[status] += 1
        
        # Обрабатываем выручку, себестоимость и товары ТОЛЬКО для доставленных заказов
        if status == "delivered":
            
            # 1. Выручка (Gross Revenue)
            tp = money_decimal(od.get("total_price", 0))
            if tp == Decimal("0.00"):
                s = Decimal("0.00")
                for it in od.get("items", []) or []:
                    s += money_decimal(it.get("sum", 0)) # Используем "sum" из order_1.json
                tp = s
            gross_revenue += tp
            
            # 2. Себестоимость (Total Cost) и учет проданных items
            order_cost = Decimal("0.00")
            for it in od.get("items", []) or []:
                # !!! Здесь name - это полное название, как "Ред Булл" !!!
                name = it.get("name", "unknown") 
                try:
                    qty = int(it.get("qty", 0)) # Используем "qty" из order_1.json
                except Exception:
                    qty = 0
                
                # Учитываем item counter только для delivered (для правильного Топ-напитка)
                items_counter[name] += qty 
                
                # Теперь эта функция корректно найдет себестоимость по полному имени
                cost_per_item = get_item_cost(name) 
                order_cost += cost_per_item * Decimal(qty)
                
            total_cost += order_cost
            
        # couriers (executor / courier_username) считаем для delivered и accepted
        courier_key = od.get("courier_username") or od.get("executor")
        if courier_key and status in ("accepted", "delivered"):
            courier_username = str(courier_key)
            if not courier_username.startswith('@') and not courier_username.isdigit():
                courier_username = f"@{courier_username}"
            couriers_counter[courier_username] += 1

    return {
        "total_orders": total_orders,
        "gross_revenue": gross_revenue,
        "total_cost": total_cost,
        "net_profit": gross_revenue - total_cost,
        "items_counter": items_counter,
        "orders_by_status": orders_by_status,
        "couriers_counter": couriers_counter,
        "orders_list": orders_list,
    }


# ---------- formatters (без изменений) ----------


def build_summary_message(agg: Dict[str, Any], header: str) -> str:
    """Использует новый формат с эмодзи и акцентами."""
    total_orders = agg["total_orders"]
    net_profit: Decimal = agg["net_profit"]
    gross_revenue: Decimal = agg["gross_revenue"]
    items_counter: Counter = agg["items_counter"]
    orders_by_status: Counter = agg["orders_by_status"]
    couriers_counter: Counter = agg["couriers_counter"]

    # Топ-напиток (только название, без количества штук)
    most_common = items_counter.most_common(1)
    top_item_text = (
        most_common[0][0] if most_common else "N/A"
    )

    # Топ-курьер
    top_courier = couriers_counter.most_common(1)
    top_courier_text = (
        f"{top_courier[0][0]} (<b>{top_courier[0][1]}</b> заказов)" if top_courier else "N/A"
    )

    # Статусы
    delivered_count = orders_by_status.get("delivered", 0)
    cancelled_count = orders_by_status.get("cancelled", 0)
    
    lines = [
        f"📊 <b>ОБЩАЯ СТАТИСТИКА {header}</b>",
        "━━━━━━━━━━━━━━━",
        format_stat_line("📋", "Всего заказов", total_orders),
        format_stat_line("✅", "Доставлено", delivered_count),
        format_stat_line("❌", "Отменено", cancelled_count),
        "━━━━━━━━━━━━━━━",
        format_stat_line("💵", "Чистая прибыль", fmt_money(net_profit)),
        format_stat_line("💰", "Общая выручка", fmt_money(gross_revenue)), 
        "━━━━━━━━━━━━━━━",
        format_stat_line("🏆", "Топ-напиток", top_item_text),
        format_stat_line("🚴", "Топ-курьер", top_courier_text),
        "",
        "📦 <b>Детализация по статусам:</b>",
    ]
    # Детализированный список статусов
    for status, cnt in sorted(orders_by_status.items(), key=lambda item: item[1], reverse=True):
        emoji = {"delivered": "✅", "accepted": "📦", "pending": "⏳", "cancelled": "❌"}.get(status, "⚪️")
        lines.append(f"  {emoji} {status.capitalize() or 'Неизвестно'}: {cnt}")
    
    lines.append("━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def build_top_drinks_message(
    items_counter: Counter, title: str, top_n: int = 15
) -> str:
    """Рейтинг напитков с медалями (сохраняет кол-во штук)."""
    lines = [
        f"🍹 <b>{title}</b>",
        "━━━━━━━━━━━━━━━",
    ]
    if not items_counter:
        lines.append("Нет данных по напиткам.")
        lines.append("━━━━━━━━━━━━━━━")
        return "\n".join(lines)

    for i, (name, qty) in enumerate(items_counter.most_common(top_n), start=1):
        if i == 1:
            prefix = "🥇"
        elif i == 2:
            prefix = "🥈"
        elif i == 3:
            prefix = "🥉"
        else:
            prefix = f"{i}."
        
        lines.append(f"{prefix} {name} — <b>{qty} шт.</b>")

    lines.append("━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def build_couriers_message(couriers_counter: Counter, title: str) -> str:
    """Рейтинг курьеров с юзернеймами и медалями."""
    lines = [
        f"🚚 <b>{title}</b>",
        "━━━━━━━━━━━━━━━",
    ]
    if not couriers_counter:
        lines.append("Нет данных по курьерам.")
        lines.append("━━━━━━━━━━━━━━━")
        return "\n".join(lines)

    sorted_couriers = couriers_counter.most_common()

    for i, (username, count) in enumerate(sorted_couriers, start=1):
        if i == 1:
            prefix = "🥇"
        elif i == 2:
            prefix = "🥈"
        elif i == 3:
            prefix = "🥉"
        else:
            prefix = f"{i}."

        display_name = username
        
        lines.append(f"{prefix} {display_name}: <b>{count}</b> заказов")

    lines.append("━━━━━━━━━━━━━━━")
    return "\n".join(lines)


async def _send_stats_message(chat_id: int, text: str, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет или редактирует сообщение со статистикой."""
    keyboard = [
        [
            InlineKeyboardButton("🔙 Назад в меню", callback_data="stats_menu_back"),
            InlineKeyboardButton("🏆 Рейтинг курьеров", callback_data="stats_couriers"),
        ]
    ]

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=context.user_data.get("stats_msg_id"),
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception:
        msg = await context.bot.send_message(
            chat_id,
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        context.user_data["stats_msg_id"] = msg.message_id # Исправлено: msg.message_id вместо msg.message.message_id


async def stats_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        if update.message:
            await update.message.reply_text("❌ You are not authorized.")
        return

    keyboard = [
        [
            InlineKeyboardButton("📅 За сегодня", callback_data="stats_daily"),
            InlineKeyboardButton("🗓️ За неделю", callback_data="stats_weekly"),
        ],
        [
            InlineKeyboardButton("🏆 Топ-напитки", callback_data="stats_top_drinks"),
            InlineKeyboardButton("🚴 Рейтинг курьеров", callback_data="stats_couriers"),
        ],
        [
            InlineKeyboardButton("🕰️ За всё время", callback_data="stats_alltime"),
        ],
    ]

    text = "📊 <b>Выберите период для просмотра статистики:</b>"
    
    if update.message:
        msg = await update.message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard)
        )
        # ИСПРАВЛЕНО: msg.message_id вместо msg.message.message_id
        context.user_data["stats_msg_id"] = msg.message_id 
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def stats_menu_back_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await stats_menu(update, context)


async def stats_daily_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = q.from_user
    if not is_admin(user.id):
        return

    today = date.today()
    paths = iter_order_files_between(today, today)
    agg = aggregate_orders(paths)
    msg = build_summary_message(
        agg, f"({today.isoformat()})"
    )
    await _send_stats_message(q.message.chat_id, msg, context)


async def stats_weekly_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = q.from_user
    if not is_admin(user.id):
        return

    today = date.today()
    start = today - timedelta(days=6)
    paths = iter_order_files_between(start, today)
    agg = aggregate_orders(paths)
    msg = build_summary_message(
        agg, f"({start.isoformat()} — {today.isoformat()})"
    )
    await _send_stats_message(q.message.chat_id, msg, context)

async def stats_alltime_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = q.from_user
    if not is_admin(user.id):
        return

    paths = iter_all_order_files()
    agg = aggregate_orders(paths)
    msg = build_summary_message(agg, "(ЗА ВСЁ ВРЕМЯ)")
    await _send_stats_message(q.message.chat_id, msg, context)


async def stats_top_drinks_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = q.from_user
    if not is_admin(user.id):
        return

    # Используем все файлы, как это было задумано для "Топ-напитков (за всё время)"
    paths = iter_all_order_files()
    agg = aggregate_orders(paths)
    msg = build_top_drinks_message(
        agg["items_counter"], "ТОП-НАПИТКИ (за всё время)", top_n=15
    )
    await _send_stats_message(q.message.chat_id, msg, context)


async def stats_couriers_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = q.from_user
    if not is_admin(user.id):
        return

    # Используем все файлы, как это было задумано для "Рейтинг курьеров (по кол-ву доставленных/принятых заказов)"
    paths = iter_all_order_files()
    agg = aggregate_orders(paths)
    msg = build_couriers_message(
        agg["couriers_counter"],
        "РЕЙТИНГ КУРЬЕРОВ (по кол-ву доставленных/принятых заказов)",
    )
    await _send_stats_message(q.message.chat_id, msg, context)


def register_handlers(application):
    application.add_handler(CommandHandler("stats", stats_menu))
    
    application.add_handler(
        CallbackQueryHandler(stats_menu_back_cb, pattern="^stats_menu_back$")
    )
    application.add_handler(
        CallbackQueryHandler(stats_daily_cb, pattern="^stats_daily$")
    )
    application.add_handler(
        CallbackQueryHandler(stats_weekly_cb, pattern="^stats_weekly$")
    )
    application.add_handler(
        CallbackQueryHandler(stats_alltime_cb, pattern="^stats_alltime$")
    )
    application.add_handler(
        CallbackQueryHandler(stats_top_drinks_cb, pattern="^stats_top_drinks$")
    )
    application.add_handler(
        CallbackQueryHandler(stats_couriers_cb, pattern="^stats_couriers$")
    )