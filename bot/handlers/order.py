import os
import json
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from bot.utils.data import add_user_message, load_user_ids
from bot.handlers.profit_report import send_profit_report
from bot.handlers.spy import notify_admins_order_status

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot.config import GROUP_CHAT_ID, ADMIN_IDS
from bot.utils.data import load_drinks
from bot.services.order_service import (
    create_order_log,             # kept for backward compatibility
    generate_random_delivery_no,
    get_next_display_no,
)

# ====== In-memory stores ======
user_sessions: Dict[int, Dict[str, Any]] = {}
user_order_progress: Dict[int, Dict[str, Any]] = {}  # waiting for quantity input
_group_message_registry: Dict[int, Tuple[int, int]] = {}
_courier_dm_registry: Dict[Tuple[int, int], int] = {}


# ====== Helpers ======

def tr(lang: str, en: str, ru: str, lv: str) -> str:
    if lang == "ru":
        return ru
    if lang == "lv":
        return lv
    return en


def fmt_money(x: Any) -> str:
    try:
        return f"{float(x):.2f}€"
    except Exception:
        return f"{x}€"

def build_maps_links(location):
    """
    Возвращает строку с ссылками на Google Maps и Waze.
    Работает и с текстовым адресом, и с геоточкой (lat/lon).
    """
    if not location:
        return None

    if isinstance(location, dict) and "latitude" in location and "longitude" in location:
        lat, lon = location["latitude"], location["longitude"]
        google = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
        waze = f"https://waze.com/ul?q={lat},{lon}&navigate=yes"
        return f"<a href='{google}'>Google Maps</a> | <a href='{waze}'>Waze</a>"

    if isinstance(location, str):
        query = location.replace(" ", "+")
        google = f"https://www.google.com/maps/search/?api=1&query={query}"
        waze = f"https://waze.com/ul?q={query}&navigate=yes"
        return f"<a href='{google}'>Google Maps</a> | <a href='{waze}'>Waze</a>"

    return None


def cart_summary(items: List[Dict[str, Any]], lang: str) -> str:
    if not items:
        return tr(lang, "_(no items)_", "_(нет товаров)_", "_(nav preču)_")
    lines = []
    for it in items:
        n = it.get("name", "-")
        q = it.get("qty", 1)
        subtotal = it.get("sum", 0)
        lines.append(f"• {n} ×{q} — {fmt_money(subtotal)}")
    return "\n".join(lines)


def now_local() -> datetime:
    return datetime.now()


def get_workday_key(dt: Optional[datetime] = None) -> str:
    dt = dt or now_local()
    # ИСПРАВЛЕНО: Сдвигаем рабочий день с 8 до 4 утра для ночной работы
    if dt.hour < 4:
        dt = dt - timedelta(days=1)
    return dt.strftime("%Y-%m-%d")


def ensure_orders_dir(day_key: str) -> str:
    base = os.path.join("data", "orders", day_key)
    os.makedirs(base, exist_ok=True)
    return base


def order_json_path(display_no: int, day_key: Optional[str] = None) -> str:
    day_key = day_key or get_workday_key()
    base = ensure_orders_dir(day_key)
    return os.path.join(base, f"order_{display_no}.json")


async def clean_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update and update.message:
            await update.message.delete()
    except Exception:
        pass
    try:
        last_id = context.user_data.get("last_bot_message_id")
        if last_id:
            await context.bot.delete_message(update.effective_chat.id, last_id)
    except Exception:
        pass

async def _delete_later(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, seconds: int = 5):
    try:
        await asyncio.sleep(seconds)
        await context.bot.delete_message(chat_id, message_id)
    except Exception:
        pass


# ====== NEW drinks.json adapter helpers ======

def _is_new_format(drinks_data: Any) -> bool:
    return isinstance(drinks_data, dict) and "categories" not in drinks_data


def iter_categories(drinks_data: Any, lang: str):
    """Yield (cat_id, localized_name) for both new and old formats."""
    if _is_new_format(drinks_data):
        for cat_id, cat in drinks_data.items():
            name_map = cat.get("name", {})
            yield cat_id, name_map.get(lang) or name_map.get("en") or next(iter(name_map.values()), cat_id)
    else:
        for cat in drinks_data.get("categories", []):
            name_map = (cat.get("name") or {})
            yield cat.get("id"), name_map.get(lang) or name_map.get("en") or next(iter(name_map.values()), cat.get("id"))


def get_drinks_in_category(drinks_data: Any, cat_id: str):
    """Return list of tuples [(drink_id, drink_obj)] for both formats."""
    result = []
    if _is_new_format(drinks_data):
        cat = drinks_data.get(cat_id) or {}
        items = cat.get("items") or {}
        for did, dobj in items.items():
            result.append((did, dobj))
    else:
        for cat in drinks_data.get("categories", []):
            if str(cat.get("id")) == str(cat_id):
                for d in cat.get("drinks", []):
                    result.append((d.get("id"), d))
                break
    return result


def get_drink_display_name(drink_obj: dict, lang: str) -> str:
    """Return localized name for both formats."""
    if "name" in drink_obj and isinstance(drink_obj["name"], dict):
        # old format
        return drink_obj["name"].get(lang) or drink_obj["name"].get("en") or next(iter(drink_obj["name"].values()), "-")
    # new format: the object itself holds langs + price
    candidates = {k: v for k, v in drink_obj.items() if k in ("en", "ru", "lv")}
    return candidates.get(lang) or candidates.get("en") or next(iter(candidates.values()), "-")


def get_drink_price(drink_obj: dict) -> float:
    try:
        return float(drink_obj.get("price", 0))
    except Exception:
        return 0.0


def find_drink_by_id(drinks_data: Any, drink_id: str) -> Optional[dict]:
    """Find a drink by id and return its object (works for both formats)."""
    if _is_new_format(drinks_data):
        for _, cat in drinks_data.items():
            for did, dobj in (cat.get("items") or {}).items():
                if str(did) == str(drink_id):
                    return dobj
    else:
        for cat in drinks_data.get("categories", []):
            for d in cat.get("drinks", []):
                if str(d.get("id")) == str(drink_id):
                    return d
    return None


# ====== Start / Flow ======
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = context.user_data.get("lang", "en")

    user_sessions[user_id] = {
        "step": "time",
        "data": {"items": [], "time": None, "region": None, "payment": None, "location": None, "note": None},
    }

    # 🔥 Сбрасываем корзину перед новым заказом
    context.user_data["cart"] = []

    msg = await update.effective_message.reply_text(
        tr(lang,
           "🕒 Please choose a delivery time:",
           "🕒 Пожалуйста, выберите время доставки:",
           "🕒 Lūdzu, izvēlieties piegādes laiku:")
        , reply_markup=build_time_keyboard(),
    )
    context.user_data["last_bot_message_id"] = msg.message_id


def build_time_keyboard() -> InlineKeyboardMarkup:
    slots: List[Tuple[str, str]] = []
    for h in range(20, 24):
        s, e = f"{h:02d}:00", f"{(h + 1) % 24:02d}:00"
        slots.append((s, e))
    for h in range(0, 8):
        s, e = f"{h:02d}:00", f"{(h + 1) % 24:02d}:00"
        slots.append((s, e))
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(f"{s}-{e}", callback_data=f"time:{s}-{e}")] for s, e in slots]
    )


# ====== Callback handler ======
async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()

    data = query.data
    user = query.from_user
    user_id = user.id
    lang = context.user_data.get("lang", "en")
    session = user_sessions.get(user_id)

    # Always load fresh drinks (auto-reload without restart)
    drinks_data = load_drinks()

    # ---- Customer flow ----
    if session:
        # time selected → delete the time message, then show categories
        if data.startswith("time:"):
            session["data"]["time"] = data.split(":", 1)[1]
            session["step"] = "category"

            try:
                await query.message.delete()
            except Exception:
                pass
            try:
                last_id = context.user_data.pop("last_bot_message_id", None)
                if last_id:
                    await context.bot.delete_message(query.message.chat_id, last_id)
            except Exception:
                pass

            kb = build_category_keyboard(drinks_data, lang)
            msg = await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=tr(lang, "🍾 Choose a category:", "🍾 Выберите категорию:", "🍾 Izvēlieties kategoriju:"),
                reply_markup=kb,
            )
            context.user_data["last_bot_message_id"] = msg.message_id
            return

        # category selected → delete category message, then show drinks
        if data.startswith("cat:"):
            cat_id = data.split(":", 1)[1]
            session["data"]["category"] = cat_id
            session["step"] = "drink"

            try:
                await query.message.delete()
            except Exception:
                pass
            try:
                last_id = context.user_data.pop("last_bot_message_id", None)
                if last_id:
                    await context.bot.delete_message(query.message.chat_id, last_id)
            except Exception:
                pass

            kb = build_drinks_keyboard(drinks_data, cat_id, lang)
            msg = await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=tr(lang, "🍸 Choose a drink:", "🍸 Выберите напиток:", "🍸 Izvēlieties dzērienu:"),
                reply_markup=kb,
            )
            context.user_data["last_bot_message_id"] = msg.message_id
            return

        # choose drink → delete drink list, ask for quantity
        if data.startswith("drink:"):
            drink_id = data.split(":", 1)[1]
            drink = find_drink_by_id(drinks_data, drink_id)
            if not drink:
                await query.answer("Drink not found", show_alert=True)
                return

            session["data"]["current_drink"] = drink
            session["step"] = "quantity"

            user_order_progress[user_id] = {
                "name": get_drink_display_name(drink, lang),
                "price": get_drink_price(drink),
                "lang": lang,
            }

            try:
                await query.message.delete()
            except Exception:
                pass
            try:
                last_id = context.user_data.pop("last_bot_message_id", None)
                if last_id:
                    await context.bot.delete_message(query.message.chat_id, last_id)
            except Exception:
                pass

            prompt = tr(
                lang,
                f"🍻 How many of {get_drink_display_name(drink, 'en')}?",
                f"🍻 Сколько {get_drink_display_name(drink, 'ru')}?",
                f"🍻 Cik {get_drink_display_name(drink, 'lv')}?",
            )
            msg = await context.bot.send_message(chat_id=query.message.chat_id, text=prompt)
            context.user_data["last_bot_message_id"] = msg.message_id
            return

        # show categories again (➕ Another drink)
        if data == "show_categories":
            items = session["data"].get("items", [])
            total = sum(float(it.get("sum", 0)) for it in items)
            # ИСПРАВЛЕНО: 15 -> 25
            diff = 25 - total if total < 25 else 0

            # 💵 Заголовок с информацией о корзине
            if diff > 0:
                balance_info = tr(
                    lang,
                    # ИСПРАВЛЕНО: 15.00€ -> 25.00€
                    f"💵 Current total: {total:.2f}€\n🧾 You need {diff:.2f}€ more to reach the minimum order (25.00€).\n\n",
                    # ИСПРАВЛЕНО: 15.00€ -> 25.00€
                    f"💵 Сейчас в корзине: {total:.2f}€\n🧾 Осталось добавить: {diff:.2f}€, чтобы оформить заказ (мин. 25.00€).\n\n",
                    # ИСПРАВЛЕНО: 15.00€ -> 25.00€
                    f"💵 Pašlaik grozā: {total:.2f}€\n🧾 Jums jāpieliek vēl {diff:.2f}€, lai sasniegtu minimālo pasūtījumu (25.00€).\n\n",
                )
            else:
                balance_info = tr(
                    lang,
                    f"💵 Current total: {total:.2f}€\n\n",
                    f"💵 Сейчас в корзине: {total:.2f}€\n\n",
                    f"💵 Pašlaik grozā: {total:.2f}€\n\n",
                )

            try:
                await query.message.delete()
            except Exception:
                pass
            try:
                last_id = context.user_data.pop("last_bot_message_id", None)
                if last_id:
                    await context.bot.delete_message(query.message.chat_id, last_id)
            except Exception:
                pass

            session["step"] = "category"
            kb = build_category_keyboard(drinks_data, lang)
            msg = await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=balance_info + tr(lang, "🍾 Choose a category:", "🍾 Выберите категорию:", "🍾 Izvēlieties kategoriju:"),
                reply_markup=kb,
                parse_mode=ParseMode.HTML,
            )
            context.user_data["last_bot_message_id"] = msg.message_id
            return

        # go to region
        if data == "cart:checkout":
            items = session["data"].get("items", [])
            total = sum(float(it.get("sum", 0)) for it in items)

            # ✅ Проверка минимальной суммы
            # ИСПРАВЛЕНО: 15 -> 25
            if total < 25:
                # ИСПРАВЛЕНО: 15 -> 25
                diff = 25 - total  # сколько не хватает до минимальной суммы
                warn_text = tr(
                    lang,
                    # ИСПРАВЛЕНО: 15.00€ -> 25.00€
                    (
                        f"❌ Minimum order amount is 25.00€.\n"
                        f"💵 Your current total is {total:.2f}€.\n"
                        f"🧾 You need to add {diff:.2f}€ more to continue.\n\n"
                        "🛍 Please add more drinks to your cart."
                    ),
                    # ИСПРАВЛЕНО: 15.00€ -> 25.00€
                    (
                        f"❌ Минимальная сумма заказа — 25.00€.\n"
                        f"💵 Сейчас в корзине: {total:.2f}€.\n"
                        f"🧾 Добавьте ещё на {diff:.2f}€, чтобы оформить заказ.\n\n"
                        "🛍 Пожалуйста, выберите дополнительные напитки."
                    ),
                    # ИСПРАВЛЕНО: 15.00€ -> 25.00€
                    (
                        f"❌ Minimālā pasūtījuma summa ir 25.00€.\n"
                        f"💵 Jūsu pašreizējā summa: {total:.2f}€.\n"
                        f"🧾 Jums jāpieliek vēl {diff:.2f}€, lai turpinātu.\n\n"
                        "🛍 Lūdzu, pievienojiet vēl dzērienus grozam."
                    ),
                )

                button_text = tr(lang, "➕ Add drinks", "➕ Добавить напитки", "➕ Pievienot dzērienus")
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton(button_text, callback_data="show_categories")]
                ])

                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=warn_text,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.HTML,
                )
                return  # ⛔ не переходим к шагу region

            # ✅ Если сумма >= 25€, продолжаем оформление как обычно
            try:
                await query.message.delete()
            except Exception:
                pass
            try:
                last_id = context.user_data.pop("last_bot_message_id", None)
                if last_id:
                    await context.bot.delete_message(query.message.chat_id, last_id)
            except Exception:
                pass

            session["step"] = "region"
            msg = await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=tr(lang, "📍 Enter your region:", "📍 Введите район:", "📍 Ievadiet savu rajonu:"),
            )
            context.user_data["last_bot_message_id"] = msg.message_id
            return

        # choose payment → delete payment message, show order summary
        if data.startswith("pay:"):
            pay = data.split(":")[1]
            pay_map = {
                "cash": tr(lang, "Cash", "Наличные", "Skaidrā naudā"),
                "card": tr(lang, "Card", "Карта", "Karte"),
            }
            session["data"]["payment"] = pay_map.get(pay, "Cash")
            session["step"] = "confirm"

            try:
                await query.message.delete()
            except Exception:
                pass
            try:
                last_id = context.user_data.pop("last_bot_message_id", None)
                if last_id:
                    await context.bot.delete_message(query.message.chat_id, last_id)
            except Exception:
                pass

            msg = await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=build_order_preview(session["data"], lang),
                parse_mode=ParseMode.HTML,
                reply_markup=build_confirm_keyboard(lang),
            )
            context.user_data["last_bot_message_id"] = msg.message_id
            return

        # confirm → delete summary then finalize OR cancel (5s notice)
        if data == "confirm:yes":
            try:
                await query.message.delete()
            except Exception:
                pass
            await finalize_order(update, context, session, lang)
            return

        if data == "confirm:no":
            try:
                await query.message.delete()
            except Exception:
                pass

            # 🧹 Очистка корзины и данных сессии
            if session:
                session["data"] = {
                    "items": [],
                    "time": None,
                    "region": None,
                    "payment": None,
                    "location": None,
                    "note": None,
                }
                session["step"] = "category"

                context.user_data["cart"] = []

            # 💬 Сообщение пользователю с кнопкой "Новый заказ"
            button_text = tr(lang, "🛒 New order", "🛒 Новый заказ", "🛒 Jauns pasūtījums")
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(button_text, callback_data="go_start_order")]
            ])

            cancel_text = tr(
                lang,
                "❌ Your order has been cancelled.\n\n🛍 You can start a new order anytime!",
                "❌ Ваш заказ отменён.\n\n🛍 Вы можете оформить новый заказ в любое время!",
                "❌ Jūsu pasūtījums tika atcelts.\n\n🛍 Jūs varat veikt jaunu pasūtījumu jebkurā laikā!",
            )

            msg = await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=cancel_text,
                reply_markup=keyboard,
            )

            context.user_data["last_bot_message_id"] = msg.message_id
            user_sessions.pop(user_id, None)
            return

    # ---- Courier/Admin actions ----
    if data.startswith("courier_action:"):
        parts = data.split(":")
        action = parts[1]
        if action == "accept":
            display_no = int(parts[2])
            await _handle_accept(update, context, display_no)
            return
        if action == "deny":
            display_no = int(parts[2])
            if user_id not in ADMIN_IDS:
                await query.answer("Only admin can deny", show_alert=True)
                return
            await _handle_deny(update, context, display_no, denied_by=user_id)
            return

    if data.startswith("courier_private:"):
        parts = data.split(":")
        sub = parts[1]
        if sub == "delivered":
            display_no = int(parts[2])
            customer_id = int(parts[3])
            await _handle_delivered(update, context, display_no, customer_id)
            return
        if sub == "cancel":
            display_no = int(parts[2])
            customer_id = int(parts[3])
            await _handle_cancel_by_courier(update, context, display_no, customer_id)
            return


# ====== Keyboards ======

def build_category_keyboard(drinks_data, lang) -> InlineKeyboardMarkup:
    rows = []
    for cat_id, cat_name in iter_categories(drinks_data, lang):
        rows.append([InlineKeyboardButton(cat_name, callback_data=f"cat:{cat_id}")])
    return InlineKeyboardMarkup(rows or [[InlineKeyboardButton("❌ None", callback_data="none")]])


def build_drinks_keyboard(drinks_data, cat_id, lang) -> InlineKeyboardMarkup:
    entries = get_drinks_in_category(drinks_data, cat_id)
    if not entries:
        return InlineKeyboardMarkup([[InlineKeyboardButton("❌ None", callback_data="none")]])

    rows = []
    for d_id, d_obj in entries:
        title = get_drink_display_name(d_obj, lang)
        price = get_drink_price(d_obj)
        rows.append([InlineKeyboardButton(f"{title} — {fmt_money(price)}", callback_data=f"drink:{d_id}")])

    rows.append([InlineKeyboardButton(tr(lang, "⬅️ Back", "⬅️ Назад", "⬅️ Atpakaļ"), callback_data="show_categories")])
    return InlineKeyboardMarkup(rows)


def build_confirm_keyboard(lang) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(tr(lang, "✅ Confirm", "✅ Подтвердить", "✅ Apstiprināt"), callback_data="confirm:yes"),
            InlineKeyboardButton(tr(lang, "❌ Cancel", "❌ Отменить", "❌ Atcelt"), callback_data="confirm:no"),
        ]
    ])


def build_payment_keyboard(lang) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(tr(lang, "💵 Cash", "💵 Наличные", "💵 Skaidrā naudā"), callback_data="pay:cash"),
            InlineKeyboardButton(tr(lang, "💳 Card", "💳 Карта", "💳 Karte"), callback_data="pay:card"),
        ]
    ])


def build_courier_group_keyboard(display_no: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Accept", callback_data=f"courier_action:accept:{display_no}"),
            InlineKeyboardButton("❌ Deny", callback_data=f"courier_action:deny:{display_no}"),
        ]
    ])


def build_courier_private_keyboard(display_no: int, customer_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Mark as delivered", callback_data=f"courier_private:delivered:{display_no}:{customer_id}"),
            InlineKeyboardButton("❌ Cancel order", callback_data=f"courier_private:cancel:{display_no}:{customer_id}"),
        ]
    ])


# ====== Order preview ======

def build_order_preview(data: Dict[str, Any], lang: str) -> str:
    items = data.get("items", [])
    summary = cart_summary(items, lang)
    region = data.get("region", "-")
    payment = data.get("payment", "-")
    time = data.get("time", "-")
    note = data.get("note") or tr(lang, "(no note)", "(без примечания)", "(bez piezīmes)")
    total = sum(float(it.get("sum", 0)) for it in items)
    location = data.get("location")
    maps = build_maps_links(location)

    base = (
        f"🧾 <b>{tr(lang, 'Order summary', 'Сводка заказа', 'Pasūtījuma kopsavilkums')}:</b>\n\n"
        f"🕒 {time}\n"
        f"📍 {region}\n\n"
        f"{summary}\n\n"
        f"💰 <b>{tr(lang, 'Total', 'Итого', 'Kopā')}:</b> {total:.2f}€\n"
        f"💳 {payment}\n"
        f"📝 {note}"
    )
    if maps:
        base += f"\n📍 {maps}"
    return base


# ====== Finalize & Persist ======
async def finalize_order(update: Update, context: ContextTypes.DEFAULT_TYPE, session: Dict[str, Any], lang: str):
    user = update.callback_query.from_user
    user_id = user.id

    display_no = get_next_display_no()
    delivery_no = generate_random_delivery_no()

    data = session["data"]
    items = data.get("items", [])
    total = sum(float(it.get("sum", 0)) for it in items)

    username_val = user.username or ""
    order_record = {
        "display_no": display_no,
        "delivery_no": delivery_no,
        "user_id": user_id,
        "username": username_val,  # no '@'
        "status": "pending",
        "time": data.get("time"),
        "region": data.get("region"),
        "location": data.get("location"),
        "note": data.get("note"),
        "payment": data.get("payment"),
        "items": items,
        "total_price": float(total),
        "created_at": now_local().isoformat(),
    }

    day_key = get_workday_key()
    json_path = order_json_path(display_no, day_key)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(order_record, f, ensure_ascii=False, indent=2)

    try:
        create_order_log(display_no, delivery_no, user_id, user.username, order_record)
    except Exception:
        pass

    placed_text = tr(
        lang,
        f"✅ Your order #{delivery_no} has been successfully placed!\n\n💵 Total: {total:.2f}€\nAwaiting courier confirmation...",
        f"✅ Ваш заказ #{delivery_no} успешно оформлен!\n\n💵 Итого: {total:.2f}€\nОжидаем подтверждение курьером...",
        f"✅ Jūsu pasūtījums #{delivery_no} ir veiksmīgi noformēts!\n\n💵 Kopā: {total:.2f}€\nGaidām kurjera apstiprinājumu...",
    )
    sent = await context.bot.send_message(user_id, placed_text, parse_mode=ParseMode.HTML)
    add_user_message(user_id, sent.message_id)

    courier_text = _format_group_order_text(order_record)
    keyboard = build_courier_group_keyboard(display_no)
    sent = await context.bot.send_message(
        GROUP_CHAT_ID,
        courier_text,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )
    _group_message_registry[sent.message_id] = (display_no, user_id)

    # 🔥 После оформленного заказа очищаем корзину пользователя
    context.user_data["cart"] = []

    user_sessions.pop(user_id, None)


def _format_group_order_text(order: Dict[str, Any]) -> str:
    items = order.get("items", [])
    items_lines = []
    for it in items:
        items_lines.append(f"  • {it.get('name')} ×{it.get('qty')} — {fmt_money(it.get('sum', 0))}")
    items_block = "\n".join(items_lines) if items_lines else "-"

    maps = build_maps_links(order.get("location"))
    location_text = "-"
    if isinstance(order.get("location"), dict):
        location_text = "(geo)"
    elif isinstance(order.get("location"), str):
        location_text = order.get("location")

    lines = [
        f"📦 <b>order #{order.get('display_no')}</b>",
        f"🆔 <b>{order.get('delivery_no')}</b>",
        "",
        f"🕒 <b>Time:</b> {order.get('time')}",
        "🍹 <b>Items:</b>",
        items_block,
        "",
        f"💳 <b>Payment:</b> {order.get('payment')}",
        f"💵 <b>Total:</b> {float(order.get('total_price') or 0):.2f}€",
        "",
        f"📝 <b>Note:</b> {order.get('note') or '-'}",
        f"📍 <b>Region:</b> {order.get('region') or '-'}",
        f"📌 <b>Location:</b> {location_text}",
    ]
    if maps:
        lines.append(f"📍 {maps}")
    return "\n".join(lines)


# ====== Text input handler ======
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user_id = update.effective_user.id
    lang = context.user_data.get("lang", "en")
    session = user_sessions.get(user_id)

    text = update.message.text.strip()

    # Quantity
    if user_id in user_order_progress:
        drink_info = user_order_progress.pop(user_id)
        try:
            qty = int(text)
            if qty <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                tr(lang, "❌ Please enter a valid number.", "❌ Пожалуйста, введите корректное число.", "❌ Lūdzu, ievadiet derīgu skaitli.")
            )
            return

        await clean_chat(update, context)

        total_price = qty * float(drink_info["price"])
        name = drink_info["name"]

        confirm_msg = tr(
            lang,
            f"✅ You added {qty} × {name} = {total_price:.2f}€ to the cart 🛒",
            f"✅ Вы добавили {qty} × {name} = {total_price:.2f}€ в корзину 🛒",
            f"✅ Jūs pievienojāt {qty} × {name} = {total_price:.2f}€ grozam 🛒",
        )
        sent = await update.effective_chat.send_message(confirm_msg)
        add_user_message(user_id, sent.message_id)

        cart = context.user_data.get("cart", [])
        cart.append({"name": name, "qty": qty, "price": float(drink_info["price"]), "sum": total_price})
        context.user_data["cart"] = cart
        if session:
            session["data"]["items"] = cart

        buttons = [[
            InlineKeyboardButton(tr(lang, "➕ Another drink", "➕ Ещё напиток", "➕ Vēl viens dzēriens"), callback_data="show_categories"),
            InlineKeyboardButton(tr(lang, "✅ Checkout", "✅ Оформить заказ", "✅ Apstiprināt pasūtījumu"), callback_data="cart:checkout"),
        ]]
        msg = await update.effective_chat.send_message(
            tr(lang, "What would you like to do next?", "Что делаем дальше?", "Ko vēlaties darīt tālāk?"),
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        context.user_data["last_bot_message_id"] = msg.message_id
        return

    # Region → Location → Note → Payment
    if session and session.get("step") == "region":
        await clean_chat(update, context)
        session["data"]["region"] = text
        session["step"] = "location"

        msg = await update.effective_chat.send_message(
            tr(lang,
               "📌 Send your location (📎 → Location) or type your address:",
               "📌 Отправьте геолокацию (📎 → Геопозиция) или введите адрес:",
               "📌 Nosūtiet atrašanās vietu (📎 → Location) vai ievadiet adresi:")
        )
        context.user_data["last_bot_message_id"] = msg.message_id
        return

    if session and session.get("step") == "location":
        await clean_chat(update, context)
        session["data"]["location"] = text
        session["step"] = "note"

        msg = await update.effective_chat.send_message(
            tr(lang,
               "📝 Enter a note for courier (or type 'skip'):",
               "📝 Добавьте примечание для курьера (или введите 'skip'):",
               "📝 Pievienojiet piezīmi kurjeram (vai rakstiet 'skip'):")
        )
        context.user_data["last_bot_message_id"] = msg.message_id
        return

    if session and session.get("step") == "note":
        await clean_chat(update, context)
        note = text
        if note.lower() == "skip":
            note = None
        session["data"]["note"] = note
        session["step"] = "payment"

        msg = await update.effective_chat.send_message(
            tr(lang, "💳 Choose payment method:", "💳 Выберите способ оплаты:", "💳 Izvēlieties maksājuma veidu:"),
            reply_markup=build_payment_keyboard(lang)
        )
        context.user_data["last_bot_message_id"] = msg.message_id
        return


# ====== Location (Telegram geo) handler ======
async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = context.user_data.get("lang", "en")
    session = user_sessions.get(user_id)
    loc = update.message.location if update.message else None

    if session and session.get("step") == "location" and loc:
        await clean_chat(update, context)
        session["data"]["location"] = {"latitude": loc.latitude, "longitude": loc.longitude}
        session["step"] = "note"
        msg = await update.effective_chat.send_message(
            tr(lang,
               "📝 Enter a note for courier (or type 'skip'):",
               "📝 Добавьте примечание для курьера (или введите 'skip'):",
               "📝 Pievienojiet piezīmi kurjeram (vai rakstiet 'skip'):")
        )
        context.user_data["last_bot_message_id"] = msg.message_id
        return


# ====== Courier/Admin actions ======
async def _load_order(display_no: int) -> Optional[Dict[str, Any]]:
    path = order_json_path(display_no)
    if not os.path.exists(path):
        day_today = get_workday_key()
        day_prev = get_workday_key(now_local() - timedelta(days=1))
        for dk in (day_today, day_prev):
            p = order_json_path(display_no, dk)
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_order(order: Dict[str, Any]):
    dk = get_workday_key(datetime.fromisoformat(order.get("created_at"))) if order.get("created_at") else get_workday_key()
    path = order_json_path(order["display_no"], dk)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(order, f, ensure_ascii=False, indent=2)


async def _handle_accept(update: Update, context: ContextTypes.DEFAULT_TYPE, display_no: int):
    from bot.utils.data import load_user_ids  # используем правильную загрузку из data.py

    query = update.callback_query
    courier = query.from_user
    order = await _load_order(display_no)
    if not order:
        await query.answer("Order not found", show_alert=True)
        return
    if order.get("status") not in ("pending",):
        await query.answer("Already processed", show_alert=True)
        return

    order["status"] = "accepted"
    order["accepted_at"] = now_local().isoformat()
    order["courier_id"] = courier.id
    order["courier_username"] = courier.username
    order["courier_name"] = courier.full_name or courier.first_name
    _save_order(order)

    try:
        msg_id = query.message.message_id
        _group_message_registry.pop(msg_id, None)
        await context.bot.delete_message(chat_id=query.message.chat_id, message_id=msg_id)
    except Exception:
        pass

    # --- resolve client's username via data/user_ids.json if missing in order ---
    def _resolve_username_from_file(user_id: int) -> Optional[str]:
        try:
            data = load_user_ids()
            if str(user_id) in data:
                user_entry = data[str(user_id)]
                if isinstance(user_entry, dict):  # {"@name": "lang"}
                    keys = list(user_entry.keys())
                    if keys:
                        return keys[0].lstrip("@")
                elif isinstance(user_entry, str) and user_entry.startswith("@"):
                    return user_entry.lstrip("@")
        except Exception as e:
            print(f"[resolve_username_from_file] Error: {e}")
        return None

    username = order.get("username") or _resolve_username_from_file(order["user_id"])

    if isinstance(username, str) and username:
        from_display = f"@{username.lstrip('@')}"
    else:
        from_display = f"<a href='tg://user?id={order['user_id']}'>{order['user_id']}</a>"

    items_block = "\n".join([
        f"  • {it.get('name')} ×{it.get('qty')} — {fmt_money(it.get('sum', 0))}"
        for it in order.get("items", [])
    ])
    maps = build_maps_links(order.get("location"))

    dm_text = (
        "🚗 <b>You accepted the order:</b>\n\n"
        f"📦 <b>Order #{order['display_no']}</b>\n"
        f"👤 <b>From:</b> {from_display}\n"
        f"👤 <b>Customer Order No:</b> {order['delivery_no']}\n"
        f"⏰ <b>Time:</b> {order.get('time')}\n"
        "🍹 <b>Items:</b>\n"
        f"{items_block}\n\n"
        f"💳 <b>Payment:</b> {order.get('payment')}\n"
        f"💵 <b>Total:</b> {float(order.get('total_price') or 0):.2f}€\n\n"
        f"📍 <b>Region:</b> {order.get('region') or '-'}\n"
        f"📝 <b>Note:</b> {order.get('note') or '-'}\n"
        f"📍 <b>Location:</b>\n"
    )
    if maps:
        dm_text += f"🔗 {maps}"

    kb = build_courier_private_keyboard(order["display_no"], order["user_id"])
    sent = await context.bot.send_message(
        courier.id,
        dm_text,
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
        disable_web_page_preview=True,
    )
    _courier_dm_registry[(courier.id, order["display_no"])] = sent.message_id

    # --- resolve customer's language using data/user_ids.json ---
    def _resolve_lang_from_file(user_id: int) -> str:
        try:
            data = load_user_ids()
            if str(user_id) in data:
                user_entry = data[str(user_id)]
                if isinstance(user_entry, dict):
                    vals = list(user_entry.values())  # ["ru"], ["en"], ...
                    if vals:
                        return vals[0]
        except Exception as e:
            print(f"[resolve_lang_from_file] Error: {e}")
        return "en"

    courier_display = f"@{courier.username}" if courier.username else "courier"
    user_lang = _resolve_lang_from_file(order["user_id"])

    # --- translated message to client ---
    text_accept = tr(
        user_lang,
        f"✅ Your order #{order['delivery_no']} has been accepted by {courier_display}.",
        f"✅ Ваш заказ #{order['delivery_no']} был принят {courier_display}.",
        f"✅ Jūsu pasūtījumu #{order['delivery_no']} pieņēma {courier_display}.",
    )

    sent = await context.bot.send_message(order["user_id"], text_accept, parse_mode=ParseMode.HTML)
    add_user_message(order["user_id"], sent.message_id)

    # --- SpyMode: уведомляем админов, что заказ принят курьером ---
    try:
        spy_order = dict(order)
        # отображаемое имя клиента, которое мы уже посчитали выше
        spy_order["from"] = from_display

        await notify_admins_order_status(
            context=context,
            display_no=order["display_no"],
            order_data=spy_order,
            action="accepted",
            actor_text=courier_display,  # кто принял
        )
    except Exception as e:
        logging.warning(f"[spy] failed to notify admins on accept: {e}")

async def _handle_deny(update: Update, context: ContextTypes.DEFAULT_TYPE, display_no: int, denied_by: int):
    query = update.callback_query
    order = await _load_order(display_no)
    if not order:
        await query.answer("Order not found", show_alert=True)
        return
    if order.get("status") not in ("pending",):
        await query.answer("Already processed", show_alert=True)
        return

    order["status"] = "denied"
    order["denied_at"] = now_local().isoformat()
    order["denied_by"] = denied_by
    _save_order(order)

    try:
        msg_id = query.message.message_id
        _group_message_registry.pop(msg_id, None)
        await context.bot.delete_message(chat_id=query.message.chat_id, message_id=msg_id)
    except Exception:
        pass

    await context.bot.send_message(order["user_id"], "❌ Your order was cancelled by admin.")

    # --- SpyMode: заказ отклонён админом ---
    try:
        admin_user = query.from_user
        admin_display = (
            f"@{admin_user.username}"
            if admin_user.username
            else (admin_user.full_name or str(admin_user.id))
        )

        def _resolve_username_from_file(user_id: int):
            try:
                data = load_user_ids()
                if str(user_id) in data:
                    record = data[str(user_id)]
                    if isinstance(record, dict):
                        k = list(record.keys())
                        if k:
                            return k[0].lstrip("@")
                    elif isinstance(record, str):
                        return record.lstrip("@")
            except Exception as e:
                logging.warning(f"[spy deny username lookup] {e}")
            return None

        username = order.get("username") or _resolve_username_from_file(order["user_id"])

        if isinstance(username, str) and username:
            from_display = f"@{username}"
        else:
            from_display = f"tg://user?id={order['user_id']}"

        spy_order = dict(order)
        spy_order["from"] = from_display

        await notify_admins_order_status(
            context=context,
            display_no=order["display_no"],
            order_data=spy_order,
            action="denied",
            actor_text=admin_display,
        )
    except Exception as e:
        logging.warning(f"[spy] failed to notify admins on deny: {e}")

    try:
        session = user_sessions.get(order["user_id"])
        if session:
            session["data"] = {
                "items": [],
                "time": None,
                "region": None,
                "payment": None,
                "location": None,
                "note": None,
            }
            session["step"] = "category"
            print(f"[INFO] Cleared cart for user {order['user_id']} after admin cancel")
    except Exception as e:
        print(f"[WARN] Failed to clear cart after admin cancel: {e}")


async def _handle_delivered(update: Update, context: ContextTypes.DEFAULT_TYPE, display_no: int, customer_id: int):
    from bot.utils.data import load_user_ids, load_user_messages, clear_user_messages

    query = update.callback_query
    courier = query.from_user
    order = await _load_order(display_no)
    if not order:
        await query.answer("Order not found", show_alert=True)
        return
    if order.get("courier_id") != courier.id:
        await query.answer("Not your order", show_alert=True)
        return
    if order.get("status") != "accepted":
        await query.answer("Invalid status", show_alert=True)
        return

    order["status"] = "delivered"
    order["delivered_at"] = now_local().isoformat()
    _save_order(order)

    await send_profit_report(order, context)

    # --- SpyMode: уведомляем админов, что заказ доставлен ---
    try:
        def _resolve_username_from_file(user_id: int) -> Optional[str]:
            try:
                data = load_user_ids()
                if str(user_id) in data:
                    user_entry = data[str(user_id)]
                    if isinstance(user_entry, dict):
                        keys = list(user_entry.keys())
                        if keys:
                            return keys[0].lstrip("@")
                    elif isinstance(user_entry, str) and user_entry.startswith("@"):
                        return user_entry.lstrip("@")
            except Exception as e:
                logging.warning(f"[resolve_username_from_file in delivered] Error: {e}")
            return None

        username = order.get("username") or _resolve_username_from_file(customer_id)

        if isinstance(username, str) and username:
            from_display = f"@{username.lstrip('@')}"
        else:
            from_display = f"tg://user?id={customer_id}"

        courier_username = order.get("courier_username")
        courier_display = (
            f"@{courier_username}"
            if courier_username
            else (courier.full_name or str(courier.id))
        )

        spy_order = dict(order)
        spy_order["from"] = from_display

        await notify_admins_order_status(
            context=context,
            display_no=order["display_no"],
            order_data=spy_order,
            action="delivered",
            actor_text=courier_display,
        )
    except Exception as e:
        logging.warning(f"[spy] failed to notify admins on delivered: {e}")


    # --- язык клиента ---
    def _resolve_lang_from_file(user_id: int) -> str:
        try:
            data = load_user_ids()
            if str(user_id) in data:
                user_entry = data[str(user_id)]
                if isinstance(user_entry, dict):
                    vals = list(user_entry.values())
                    if vals:
                        return vals[0]
        except Exception as e:
            print(f"[resolve_lang_from_file] Error: {e}")
        return "en"

    user_lang = _resolve_lang_from_file(customer_id)

    # --- текст доставлено ---
    text_delivered = tr(
        user_lang,
        f"✅ Your order #{order['delivery_no']} has been delivered! Enjoy! 🎉",
        f"✅ Ваш заказ #{order['delivery_no']} был доставлен! Приятного вечера! 🎉",
        f"✅ Jūsu pasūtījums #{order['delivery_no']} ir piegādāts! Lai jauka diena! 🎉",
    )

    # отправляем сообщение клиенту
    await context.bot.send_message(customer_id, text_delivered, parse_mode=ParseMode.HTML)

    # --- удаляем все старые сообщения ---
    try:
        messages = load_user_messages()
        to_delete = messages.get(str(customer_id), [])
        for mid in to_delete:
            try:
                await context.bot.delete_message(customer_id, mid)
            except Exception:
                continue
        clear_user_messages(customer_id)
    except Exception as e:
        print(f"[handle_delivered] cleanup error: {e}")

    # удаляем сообщение курьера
    try:
        await context.bot.delete_message(chat_id=query.message.chat_id, message_id=query.message.message_id)
    except Exception:
        pass

async def _handle_cancel_by_courier(update: Update, context: ContextTypes.DEFAULT_TYPE, display_no: int, customer_id: int):
    query = update.callback_query
    courier = query.from_user
    order = await _load_order(display_no)
    if not order:
        await query.answer("Order not found", show_alert=True)
        return
    if order.get("courier_id") != courier.id:
        await query.answer("Not your order", show_alert=True)
        return
    if order.get("status") not in ("accepted",):
        await query.answer("Invalid status", show_alert=True)
        return

    order["status"] = "cancelled"
    order["cancelled_at"] = now_local().isoformat()
    order["cancelled_by"] = courier.id
    _save_order(order)

    try:
        await context.bot.delete_message(chat_id=query.message.chat_id, message_id=query.message.message_id)
    except Exception:
        pass

    await context.bot.send_message(customer_id, "❌ Your order was cancelled by courier.")
    
    try:
        courier_display = (
            f"@{courier.username}"
            if courier.username
            else (courier.full_name or str(courier.id))
        )

        def _resolve_username_from_file(user_id: int):
            try:
                data = load_user_ids()
                if str(user_id) in data:
                    record = data[str(user_id)]
                    if isinstance(record, dict):
                        k = list(record.keys())
                        if k:
                            return k[0].lstrip("@")
                    elif isinstance(record, str):
                        return record.lstrip("@")
            except Exception as e:
                logging.warning(f"[spy courier_cancel username lookup] {e}")
            return None

        username = order.get("username") or _resolve_username_from_file(customer_id)

        if isinstance(username, str) and username:
            from_display = f"@{username}"
        else:
            from_display = f"tg://user?id={customer_id}"

        spy_order = dict(order)
        spy_order["from"] = from_display

        await notify_admins_order_status(
            context=context,
            display_no=order["display_no"],
            order_data=spy_order,
            action="courier_cancelled",
            actor_text=courier_display,
        )
    except Exception as e:
        logging.warning(f"[spy] failed to notify admins on courier cancel: {e}")
    
    try:
        session = user_sessions.get(customer_id)
        if session:
            session["data"] = {
                "items": [],
                "time": None,
                "region": None,
                "payment": None,
                "location": None,
                "note": None,
            }
            session["step"] = "category"
            print(f"[INFO] Cleared cart for user {customer_id} after courier cancel")
    except Exception as e:
        print(f"[WARN] Failed to clear cart after courier cancel: {e}")