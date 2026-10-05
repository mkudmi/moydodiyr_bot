import asyncio
import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from moydodyr.bot.formatting import order_text
from moydodyr.bot.keyboards import MENU
from moydodyr.config import Settings
from moydodyr.db import Database
from moydodyr.integrations.n8n import PermanentWebhookError, send_n8n_lead


async def deliver_outbox(bot: Bot, db: Database, settings: Settings):
    while True:
        row = await db.next_notification()
        if row is None:
            await asyncio.sleep(1)
            continue
        try:
            data = row["payload"]
            if data["kind"] == "order":
                order = await db.order(data["order_id"])
                catalog = await db.catalog()
                markup = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Взять в работу", callback_data=f"order:{order['id']}:take"
                            )
                        ]
                    ]
                )
                await bot.send_message(
                    row["chat_id"], order_text(order, catalog), reply_markup=markup
                )
            elif data["kind"] == "photo":
                await bot.send_photo(row["chat_id"], data["photo"], caption=data["caption"])
            elif data["kind"] == "text":
                await bot.send_message(row["chat_id"], data["text"], reply_markup=MENU)
            elif data["kind"] == "webhook":
                if not settings.webhook_enabled:
                    raise PermanentWebhookError("n8n webhook is not configured")
                lead_id = await send_n8n_lead(
                    settings.n8n_webhook_url,
                    settings.n8n_webhook_secret,
                    data["lead"],
                )
                if data.get("order_id") is not None:
                    await db.save_external_lead_id(data["order_id"], lead_id)
            else:
                await bot.send_message(row["chat_id"], data["text"])
            await db.mark_delivered(row["id"])
        except PermanentWebhookError as error:
            await db.fail_notification(row["id"], str(error))
            logging.error("Permanently failed outbox item %s: %s", row["id"], error)
        except Exception as error:
            # Webhook errors are deliberately reduced to their message: our HTTP
            # client messages contain status/reason, but never request body or secret.
            safe_error = str(error)[:500] or type(error).__name__
            if row["attempts"] >= 10:
                await db.fail_notification(row["id"], safe_error)
                logging.error("Outbox item %s exhausted retries (%s)", row["id"], safe_error)
            else:
                await db.defer_notification(row["id"], safe_error)
                logging.warning("Failed to deliver outbox item %s (%s)", row["id"], safe_error)
