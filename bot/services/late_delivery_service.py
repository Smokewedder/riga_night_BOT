import asyncio
from datetime import datetime
from pathlib import Path
from bot.config import LATE_DELIVERY_CHECK_INTERVAL_MINUTES, PRIMARY_ADMIN_ID, ORDERS_DIR
from bot.utils.logging import log_exception

class LateDeliveryNotifier:
    def __init__(self, bot, scheduler):
        self.bot = bot
        self.scheduler = scheduler
        self.jobs = {}  # order_id: job

    def schedule_late_check(self, order_number, today_key):
        job_id = f"late_delivery_{today_key}_{order_number}"
        if job_id in self.jobs:
            self.cancel_job(order_number, today_key)
        self.jobs[job_id] = self.scheduler.add_job(
            self.late_check_job,
            "interval",
            minutes=LATE_DELIVERY_CHECK_INTERVAL_MINUTES,
            args=[order_number, today_key, 1],
            id=job_id,
            replace_existing=True
        )

    def cancel_job(self, order_number, today_key):
        job_id = f"late_delivery_{today_key}_{order_number}"
        if job_id in self.jobs:
            self.scheduler.remove_job(job_id)
            del self.jobs[job_id]

    async def late_check_job(self, order_number, today_key, run_count):
        try:
            order_path = ORDERS_DIR / today_key / f"order_{order_number}.json"
            if not order_path.exists():
                self.cancel_job(order_number, today_key)
                return
            import json
            with open(order_path, "r", encoding="utf-8") as f:
                order = json.load(f)
            status = order.get("status", "").lower()
            if status in ("delivered", "cancelled", "denied"):
                self.cancel_job(order_number, today_key)
                return
            mins_late = LATE_DELIVERY_CHECK_INTERVAL_MINUTES * run_count
            await self.notify_admin(order, mins_late)
            # Reschedule with incremented run_count
            job_id = f"late_delivery_{today_key}_{order_number}"
            self.scheduler.add_job(
                self.late_check_job,
                "interval",
                minutes=LATE_DELIVERY_CHECK_INTERVAL_MINUTES,
                args=[order_number, today_key, run_count + 1],
                id=job_id,
                replace_existing=True
            )
        except Exception as e:
            log_exception(e)
            self.cancel_job(order_number, today_key)

    async def notify_admin(self, order, mins_late):
        try:
            message = self.format_message(order, mins_late)
            await self.bot.send_message(PRIMARY_ADMIN_ID, message, parse_mode="HTML", disable_web_page_preview=True)
        except Exception as e:
            log_exception(e)

    def format_message(self, order, mins_late):
        def safe(val):
            return val if val else "N/A"
        order_id = safe(order.get("order_id"))
        client = safe(order.get("from"))
        courier = safe(order.get("courier_full_name"))
        courier_id = order.get("courier_id")
        # Make courier clickable if courier_id is present
        if courier_id:
            courier = f'<a href="tg://user?id={courier_id}">{courier}</a>'
        time_window = safe(order.get("time"))
        quantity = safe(order.get("quantity"))
        payment = safe(order.get("payment"))
        region = safe(order.get("region"))
        note = safe(order.get("note"))
        status = safe(order.get("status")).upper()
        loc_data = order.get("location")
        waze = gmaps = ""
        if loc_data:
            if isinstance(loc_data, dict) and "latitude" in loc_data:
                lat, lon = loc_data["latitude"], loc_data["longitude"]
                waze = f"https://waze.com/ul?ll={lat},{lon}&navigate=yes"
                gmaps = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
            else:
                waze = f"https://waze.com/ul?q={loc_data}"
                gmaps = f"https://www.google.com/maps/search/?api=1&query={str(loc_data).replace(' ', '+')}"
        location_links = f"<a href='{waze}'>Waze</a> | <a href='{gmaps}'>Google Maps</a>" if waze and gmaps else "N/A"
        return (
            f"🚨 <b>Late Delivery:</b>\n"
            f"Order <b>#{order_id}</b> is <b>{mins_late} mins</b> late by plug {courier}!\n\n"
            f"📦 Order #{order_id} (Client: {client})\n"
            f"👤 Accepted by: {courier}\n"
            f"⏰ Time: {time_window}\n"
            f"🥦 Quantity: {quantity}\n"
            f"💳 Payment: {payment}\n"
            f"📍 Region: {region}\n"
            f"📝 Note: {note}\n"
            f"⏳ Order Status: {status}\n\n"
            f"📍 Location:\n"
            f"🔗 {location_links}"
        ) 