import json
from telegram import Update
from telegram.ext import ContextTypes
from bot.config import ADMIN_IDS
from bot.utils.data import load_user_ids
from bot.handlers.spy import set_spy_status, get_spy_status

SHOP_STATUS_FILE = "data/shop_status.json"


# ===== Вспомогательные функции =====

def is_admin(user_id: int) -> bool:
    """Проверка, является ли пользователь админом"""
    return str(user_id) in [str(a) for a in ADMIN_IDS]


def set_shop_status(is_open: bool):
    """Сохранить статус магазина в файл"""
    with open(SHOP_STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump({"open": is_open}, f)


def get_shop_status() -> bool:
    """Проверить, открыт ли магазин"""
    try:
        with open(SHOP_STATUS_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("open", False)
    except Exception:
        return False


# ===== Команды для админа =====

async def open_shop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /open — открыть магазин"""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ У вас нет прав для этой команды.")
        return

    set_shop_status(True)
    await update.message.reply_text("🟢 Магазин открыт для заказов!")

    users = load_user_ids()
    for uid, info in users.items():
        lang = next(iter(info.values()), "ru") if isinstance(info, dict) else "ru"
        text = {
            "ru": "🟢 Магазин открыт! Мы принимаем заказы 🍹",
            "en": "🟢 The shop is now open for orders! 🍹",
            "lv": "🟢 Veikals ir atvērts pasūtījumiem! 🍹",
        }.get(lang, "🟢 Магазин открыт! Мы принимаем заказы 🍹")
        try:
            await context.bot.send_message(int(uid), text)
        except Exception:
            continue


async def close_shop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /close — закрыть магазин"""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ У вас нет прав для этой команды.")
        return

    set_shop_status(False)
    await update.message.reply_text("🔴 Магазин закрыт для заказов.")


# ===== Новые команды рассылок =====

async def msg_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /msgall <текст> — отправить сообщение всем пользователям"""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ У вас нет прав для этой команды.")
        return

    if not context.args:
        await update.message.reply_text("⚠️ Использование: /msgall <текст сообщения>")
        return

    message_text = " ".join(context.args).strip()
    users = load_user_ids()

    count = 0
    for uid in users.keys():
        try:
            await context.bot.send_message(
                int(uid),
                f"{message_text}\n\n———\n🤖 <b>This is automatic system message</b>\n",
                parse_mode="HTML",
            )
            count += 1
        except Exception:
            continue

    await update.message.reply_text(f"✅ Сообщение успешно отправлено {count} пользователям.")


async def msg_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /msg @username <текст> или /msg <id> <текст>"""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ У вас нет прав для этой команды.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("⚠️ Использование: /msg @username <сообщение> или /msg <id> <сообщение>")
        return

    target = context.args[0].replace("@", "").strip()
    message_text = " ".join(context.args[1:]).strip()

    users = load_user_ids()
    target_id = None

    # 🔍 Поиск по username внутри словаря user_ids.json
    for uid, info in users.items():
        if isinstance(info, dict):
            for username, lang in info.items():
                if username.lower() == target.lower():
                    target_id = int(uid)
                    break
        if target_id:
            break

    # если не нашли по username, пробуем как ID
    if target_id is None:
        try:
            target_id = int(target)
        except ValueError:
            await update.message.reply_text("⚠️ Пользователь не найден.")
            return

    # отправляем сообщение
    try:
        await context.bot.send_message(
            target_id,
            f"{message_text}\n\n———\n🤖 <b>Это автоматическое сообщение от бота доставки</b>\n",
            parse_mode="HTML",
        )
        await update.message.reply_text(f"✅ Сообщение отправлено пользователю {target}.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Не удалось отправить сообщение: {e}")


# ===== Интерфейс для других частей бота =====

def shop_is_open() -> bool:
    """Используется в order.py для проверки перед оформлением заказа"""
    return get_shop_status()

async def spy_on_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /spyon — включить Spy Mode"""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ У вас нет прав для этой команды.")
        return

    set_spy_status(True)
    await update.message.reply_text("Spy mode on🟢")

async def spy_off_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /spyoff — выключить Spy Mode"""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ У вас нет прав для этой команды.")
        return

    set_spy_status(False)
    await update.message.reply_text("Spy mode off🔴")