import logging
from typing import Dict, Any, List, Tuple
from decimal import Decimal

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

# Импортируем PROFIT_REPORT_CHAT_ID из config.py
from bot.config import PROFIT_REPORT_CHAT_ID 
# Импортируем функции для работы с себестоимостью и Decimal из stats.py
from bot.handlers.stats import get_item_cost, money_decimal

logger = logging.getLogger(__name__)

# --- Константы распределения прибыли ---
# Курьер: 46% от чистой прибыли
COURIER_PERCENTAGE = Decimal("0.46") 
# MR. SANYA и MR. REPA делят оставшиеся 54% пополам: 54% / 2 = 27%
OWNER_SPLIT_PERCENTAGE = Decimal("0.27") 

def calculate_order_profit_detailed(order_data: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Decimal]:
    """
    Рассчитывает прибыль по каждому товару и общую чистую прибыль по заказу.
    Возвращает: (список_товаров_с_деталями, общая_чистая_прибыль)
    """
    detailed_items: List[Dict[str, Any]] = []
    total_profit = Decimal("0.00")

    items = order_data.get("items", []) or []

    for item in items:
        name = item.get("name", "unknown")
        try:
            qty = int(item.get("qty", 0))
        except Exception:
            qty = 0
            
        # Цена продажи за одну единицу
        sell_price_per_unit = money_decimal(item.get("price", 0)) 
        
        # Себестоимость за одну единицу (используем функцию из stats.py)
        cost_per_unit = get_item_cost(name) 
        
        # Расчеты
        item_revenue = sell_price_per_unit * Decimal(qty)
        item_cost = cost_per_unit * Decimal(qty)
        item_profit = item_revenue - item_cost
        
        total_profit += item_profit

        detailed_items.append({
            "name": name,
            "qty": qty,
            "buy": cost_per_unit, # Себестоимость за 1 шт.
            "sell": sell_price_per_unit, # Цена продажи за 1 шт.
            "total_profit": item_profit # Прибыль со всего количества
        })

    return detailed_items, total_profit

def format_profit_message(order_data: Dict[str, Any], detailed_items: List[Dict[str, Any]], total_profit: Decimal) -> str:
    """Форматирует сообщение для группы 'Money count'."""
    
    courier_username = order_data.get("courier_username", "N/A")
    display_no = order_data.get("display_no", "N/A")
    
    if not courier_username.startswith('@') and courier_username != "N/A":
        courier_username = f"@{courier_username}"

    # 1. Расчет распределения
    courier_share = money_decimal(total_profit * COURIER_PERCENTAGE)
    remaining_profit = total_profit - courier_share
    sanya_share = money_decimal(remaining_profit * Decimal("0.5")) # 27%
    repa_share = money_decimal(remaining_profit * Decimal("0.5")) # 27%
    
    # Корректировка, чтобы сумма долей точно совпадала с total_profit
    # Это важно из-за округления Decimal.
    # Фактическая прибыль может быть меньше суммы округленных долей,
    # поэтому просто убеждаемся, что все доли суммируются в total_profit.
    if total_profit > 0:
        # Корректируем Репу, чтобы избежать ошибок округления
        repa_share = money_decimal(total_profit - courier_share - sanya_share)
        
    
    # 2. Формирование сообщения
    lines = [
        f"💸 <b>ОТЧЕТ ПО ЗАКАЗУ #{display_no}</b> 💸",
        "━━━━━━━━━━━━━━━",
        f"🚚 Курьер: <b>{courier_username}</b>",
    ]

    # 3. Детализация по товарам
    lines.append("\n<b>Детализация по товарам:</b>")
    for item in detailed_items:
        qty_str = f"X{item['qty']} " if item['qty'] > 1 else ""
        lines.append(f"<b>{qty_str}{item['name']}</b>")
        lines.append(f"  ➖ Buy: {item['buy']:.2f}€/шт.")
        lines.append(f"  ➕ Sell: {item['sell']:.2f}€/шт.")
        lines.append(f"  ➡️ Total Profit: <b>{item['total_profit']:.2f}€</b>")
    
    lines.append("━━━━━━━━━━━━━━━")
    
    # 4. Итоговое распределение
    lines.extend([
        f"📈 <b>Общая чистая прибыль:</b> <u>{total_profit:.2f}€</u>",
        "----------------------------------",
        f"🚴 Курьеру ({COURIER_PERCENTAGE*100:.0f}%): <b>{courier_share:.2f}€</b>",
        f"🧑‍💻 MR. SANYA ({OWNER_SPLIT_PERCENTAGE*100:.0f}%): <b>{sanya_share:.2f}€</b>",
        f"🧑‍💻 MR REPA ({OWNER_SPLIT_PERCENTAGE*100:.0f}%): <b>{repa_share:.2f}€</b>",
    ])
    
    return "\n".join(lines)


async def send_profit_report(order_data: Dict[str, Any], context: ContextTypes.DEFAULT_TYPE):
    """
    Основная функция: рассчитывает прибыль и отправляет отчет в чат.
    """
    try:
        if not order_data:
            logger.error("Attempted to send profit report with empty order data.")
            return
        
        # 1. Рассчитываем
        detailed_items, total_profit = calculate_order_profit_detailed(order_data)

        if total_profit <= 0:
            logger.warning(f"Order {order_data.get('delivery_no')} has zero or negative profit. Skipping detailed report.")
            return

        # 2. Форматируем
        message_text = format_profit_message(order_data, detailed_items, total_profit)

        # 3. Отправляем в чат
        await context.bot.send_message(
            chat_id=PROFIT_REPORT_CHAT_ID,
            text=message_text,
            parse_mode=ParseMode.HTML
        )
        logger.info(f"Successfully sent profit report for order {order_data.get('display_no')}.")

    except Exception as e:
        logger.error(f"Failed to send profit report for order {order_data.get('display_no')}: {e}")