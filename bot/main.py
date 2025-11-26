import asyncio
import logging
from telegram import BotCommand, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode
from bot.utils.data import load_user_ids, load_drinks, add_user_message

# --- Project config ---
from bot.config import BOT_TOKEN
from bot.handlers.order import handle_text, handle_location
from bot.handlers.start import (
    start_entry,
    handle_lang_choice,
    handle_start_callback,
    check_subscription,
)
from bot.handlers.order import (
    start_command,
    handle_callback_query,
)
# ✅ Оставляем только те админ-команды, которые реально есть в admin.py
from bot.handlers.admin import (
    msg_command,
    msg_all_command,
    open_shop_command,
    close_shop_command,
    spy_on_command,
    spy_off_command,
)

try:
    from bot.handlers.stats import register_handlers as register_stats_handlers
except Exception:
    register_stats_handlers = None

# --- Logging setup ---
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

for name in ("telegram", "telegram.ext", "httpx", "apscheduler", "urllib3"):
    logging.getLogger(name).setLevel(logging.WARNING)

logging.getLogger("telegram._bot").setLevel(logging.WARNING)
logging.getLogger("telegram.ext._application").setLevel(logging.ERROR)

# ==========================================================
# 🧾 /menu — красиво оформленное меню из drinks.json с учётом языка
# ==========================================================
async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет пользователю красиво оформленное меню напитков из drinks.json"""
    user_id = update.effective_user.id

    # 1) Определяем язык пользователя
    try:
        user_data = load_user_ids()
        if str(user_id) in user_data:
            entry = user_data[str(user_id)]
            if isinstance(entry, dict):
                vals = list(entry.values())
                user_lang = vals[0] if vals else "ru"
            else:
                user_lang = "ru"
        else:
            user_lang = "ru"
    except Exception as e:
        print(f"[menu_command] language detect error: {e}")
        user_lang = "ru"

    # 2) Загружаем drinks.json
    try:
        drinks_data = load_drinks()
    except Exception as e:
        print(f"[menu_command] load_drinks error: {e}")
        await update.message.reply_text("❌ Меню временно недоступно.")
        return

    # 3) Эмодзи категорий
    category_emojis = {
        "Beer": "🍺",
        "Vodka": "🥶",
        "Whiskey": "🥃",
        "Champagne": "🍾",
        "Energy Drinks": "⚡",
        "Wine": "🍷",
        "Tequila": "🌵",
        "Rum": "🏴‍☠️",
        "Gin": "🍸",
        "Liqueur": "🍹",
    }

    # 4) Компактный заголовок
    headers = {
        "ru": "🍸 <b>Меню напитков</b>\n━━━━━━━━━━\n",
        "en": "🍸 <b>Drinks Menu</b>\n━━━━━━━━━━\n",
        "lv": "🍸 <b>Dzērienu ēdienkarte</b>\n━━━━━━━━━━\n",
    }
    header = headers.get(user_lang, headers["ru"])

    lines = [header]

    # 5) Формирование списка
    for cat_key, cat_info in drinks_data.items():
        cat_name = cat_info["name"].get(user_lang, cat_info["name"].get("en", cat_key))
        emoji = category_emojis.get(cat_name, "🍹")
        lines.append(f"<b>{emoji} {cat_name}</b>")
        lines.append("──────────")

        for item_key, item in cat_info["items"].items():
            name = item.get(user_lang, item.get("en", item_key))
            price = item.get("price", 0)
            lines.append(f"▫️ <b>{name}</b> — {price:.2f}€")

        lines.append("")

    text = "\n".join(lines)

    # 6) Отправляем и запоминаем для последующей очистки
    sent = await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    try:
        add_user_message(user_id, sent.message_id)
    except Exception as e:
        print(f"[menu_command] add_user_message error: {e}")

# ==========================================================

# --- Startup commands ---
async def set_bot_commands(application):
    """Устанавливаем команды, видимые в меню Telegram"""
    commands = [
        BotCommand("start", "Start bot"),
        BotCommand("menu", "🍸 Open drinks menu"),
    ]
    await application.bot.set_my_commands(commands)

# --- Base handlers ---
def register_handlers(application):
    """Регистрация всех хендлеров"""

    # ✅ 1-й: /start и /menu
    application.add_handler(CommandHandler("start", start_entry))
    application.add_handler(CommandHandler("menu", menu_command))

    # ✅ 2-й: Специфичные Callback'и, которые не должны перехватываться
    application.add_handler(CallbackQueryHandler(handle_lang_choice, pattern="^lang_"))
    application.add_handler(CallbackQueryHandler(check_subscription, pattern="^check_subscribe$"))
    application.add_handler(CallbackQueryHandler(handle_start_callback, pattern="^go_start_order$"))

    # ✅ 3-й: Специфичные обработчики статистики (из stats.py)
    # *Теперь они будут регистрироваться раньше общего обработчика*
    if register_stats_handlers:
        register_stats_handlers(application) # Регистрирует stats_daily, stats_weekly и т.д.

    # ✅ 4-й: Админ-команды
    application.add_handler(CommandHandler("msg", msg_command))
    application.add_handler(CommandHandler("msgall", msg_all_command))
    application.add_handler(CommandHandler("open", open_shop_command))
    application.add_handler(CommandHandler("close", close_shop_command))
    application.add_handler(CommandHandler("spyon", spy_on_command))
    application.add_handler(CommandHandler("spyoff", spy_off_command))

    # ✅ 5-й: Текстовые сообщения и геолокация
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(filters.LOCATION, handle_location))

    # ⚠️ 6-й: Общий CallbackHandler для логики заказов
    application.add_handler(CallbackQueryHandler(handle_callback_query))

# --- Main bot routine ---
async def main():
    logging.info("🚀 Starting Delivery Bot...")

    application = ApplicationBuilder().token(BOT_TOKEN).build()
    register_handlers(application)
    await set_bot_commands(application)

    logging.info("✅ Bot started successfully!")
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    await asyncio.Event().wait()


import sys
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

if __name__ == "__main__":
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(main())

    try:
        print("🚀 Bot is running... (press Ctrl+C to stop)")
        loop.run_forever()
    except KeyboardInterrupt:
        print("\n🛑 Bot stopped manually.")
