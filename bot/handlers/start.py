# bot/handlers/start.py — обновлённая версия с автозагрузкой языка из user_ids.json

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from bot.handlers.admin import shop_is_open

import os, json

USER_DB_PATH = os.path.join("data", "user_ids.json")

def load_user_lang(user_id: int) -> str:
    """Загружает язык пользователя из базы, если он есть"""
    if not os.path.exists(USER_DB_PATH):
        return None
    try:
        with open(USER_DB_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        record = data.get(str(user_id))
        if isinstance(record, dict):
            for _, lang in record.items():
                return lang
    except Exception:
        return None
    return None

def save_user(user_id: int, username: str, lang: str):
    """Сохраняет или обновляет пользователя в базе user_ids.json"""
    os.makedirs(os.path.dirname(USER_DB_PATH), exist_ok=True)
    data = {}
    if os.path.exists(USER_DB_PATH):
        try:
            with open(USER_DB_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}

    uname = f"@{username}" if username and not str(username).startswith("@") else (username or f"user_{user_id}")
    if uname and not uname.startswith("@"):
        uname = f"@{uname}"

    data[str(user_id)] = {uname: (lang or "en")}

    with open(USER_DB_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

from bot.handlers.order import start_command
from bot.config import CHANNEL_ID_TO_CHECK, SUBSCRIBE_LINK

# --- Localization data ---
LANGS = {
    "lv": "🇱🇻 Latviešu",
    "en": "🇬🇧 English",
    "ru": "🇷🇺 Русский",
}

TEXTS = {
    "choose_lang": {
        "lv": "Lūdzu, izvēlies valodu 👇",
        "en": "Please select your language 👇",
        "ru": "Пожалуйста, выберите язык 👇",
    },
    "subscribe_required": {
        "lv": "❗️Lūdzu, abonējiet mūsu kanālu, lai izmantotu botu.",
        "en": "❗️Please subscribe to our channel to use the bot.",
        "ru": "❗️Пожалуйста, подпишитесь на наш канал, чтобы использовать бота.",
    },
    "welcome": {
        "lv": (
            "🍾 <b>Laipni lūdzam piegādes botā!</b> 🍾\n\n"
            "Lai veiktu pasūtījumu, mums nepieciešama šāda informācija:\n\n"
            "📍 Piegādes adrese\n"
            "⏱ Piegādes laiks\n"
            "🥃 Dzēriens\n\n"
            "🚚 Sāksim! 🚚"
        ),
        "en": (
            "🍾 <b>Welcome to Delivery Bot!</b> 🍾\n\n"
            "To place your order, we just need a few details:\n\n"
            "📍 Delivery address\n"
            "⏱ Delivery time range\n"
            "🥃 Drink\n\n"
            "🚚 Let's Start! 🚚"
        ),
        "ru": (
            "🍾 <b>Добро пожаловать в Delivery Bot!</b> 🍾\n\n"
            "Для оформления заказа нам понадобятся следующие данные:\n\n"
            "📍 Адрес доставки\n"
            "⏱ Временной интервал\n"
            "🥃 Напиток\n\n"
            "🚚 Поехали! 🚚"
        ),
    },
}

# --- i18n helper ---
def tr(key: str, lang: str) -> str:
    return TEXTS.get(key, {}).get(lang, TEXTS.get(key, {}).get("en", key))

# --- Helpers ---

def make_lang_keyboard():
    buttons = [[InlineKeyboardButton(name, callback_data=f"lang_{code}")] for code, name in LANGS.items()]
    return InlineKeyboardMarkup(buttons)

def make_order_keyboard(lang="en"):
    text = {
        "lv": "🚚 Veikt pasūtījumu 🚚",
        "en": "🚚 Make Order 🚚",
        "ru": "🚚 Сделать заказ 🚚",
    }.get(lang, "🚚 Make Order 🚚")
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data="go_start_order")]])

def subscribe_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📲 Subscribe", url=SUBSCRIBE_LINK)],
        [InlineKeyboardButton("✅ Check Subscription", callback_data="check_subscribe")],
    ])

# --- Handlers ---

async def start_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # 🚫 Проверка, открыт ли магазин
    if not shop_is_open():
        # Определяем язык (если не выбран — английский)
        stored_lang = context.user_data.get("lang", "en")
        msg_text = {
            "ru": "🔴 Магазин сейчас закрыт. Увидимся позже!",
            "en": "🔴 The shop is currently closed. See you later!",
            "lv": "🔴 Veikals pašlaik ir slēgts. Tiksimies vēlāk!",
        }.get(stored_lang, "🔴 The shop is currently closed. See you later!")

        await update.message.reply_text(msg_text)
        return

    # загружаем язык, если он сохранён ранее
    stored_lang = load_user_lang(user.id)
    if stored_lang:
        context.user_data["lang"] = stored_lang

    lang = context.user_data.get("lang", stored_lang or "en")
    save_user(user.id, getattr(user, "username", None), lang)

    if not stored_lang:
        await update.message.reply_text("🌍 " + TEXTS["choose_lang"]["en"], reply_markup=make_lang_keyboard())
        return

    try:
        await update.message.delete()
    except Exception:
        pass

    await check_subscription(update, context, lang)

async def handle_lang_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang_code = query.data.split("_")[1]
    context.user_data["lang"] = lang_code

    user = update.effective_user
    save_user(user.id, getattr(user, "username", None), lang_code)

    await query.answer()
    await query.edit_message_text(text=tr("choose_lang", lang_code) + " ✅")
    await check_subscription(update, context, lang_code)

async def check_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE, lang="en"):
    user = update.effective_user
    bot = context.bot
    try:
        member = await bot.get_chat_member(CHANNEL_ID_TO_CHECK, user.id)
        is_subscribed = member.status in ["member", "administrator", "creator"]
    except Exception:
        is_subscribed = False

    if not is_subscribed:
        await bot.send_message(chat_id=user.id, text=tr("subscribe_required", lang), reply_markup=subscribe_keyboard())
        return

    await bot.send_message(chat_id=user.id, text=tr("welcome", lang), parse_mode=ParseMode.HTML, reply_markup=make_order_keyboard(lang))
    if update.callback_query:
        try:
            await update.callback_query.message.delete()
        except Exception:
            pass

async def handle_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_command(update, context)

        # 🚫 Проверяем, открыт ли магазин
    if not shop_is_open():
        lang = context.user_data.get("lang", "en")
        msg_text = {
            "ru": "🔴 Магазин сейчас закрыт. Увидимся позже! Мы работаем с 20:00 до 8:00.",
            "en": "🔴 The shop is currently closed. See you later! We are open from 20:00 to 08:00.",
            "lv": "🔴 Veikals pašlaik ir slēgts. Tiksimies vēlāk! Mēs strādājam no 20:00 līdz 08:00.",
        }.get(lang, "🔴 The shop is currently closed. See you later!")
        await context.bot.send_message(update.effective_chat.id, msg_text)
        return
