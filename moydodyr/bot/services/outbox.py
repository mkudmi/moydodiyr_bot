import asyncio
import logging
from contextlib import nullcontext
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Chat, InlineKeyboardButton, InlineKeyboardMarkup, Message

from moydodyr.bot.formatting import order_text
from moydodyr.bot.keyboards import MENU
from moydodyr.bot.services.screen import SCREEN_KEY, show_screen
from moydodyr.bot.states import Form
from moydodyr.config import Settings
from moydodyr.db import Database, PostgresStorage
from moydodyr.integrations.n8n import PermanentWebhookError, send_n8n_lead


async def deliver_outbox(bot: Bot, db: Database, settings: Settings, isolation=None):
    while True:
        row = await db.next_notification()
        if row is None:
            await asyncio.sleep(1)
            continue
        try:
            data = row["payload"]
            if data["kind"] in {"order", "order_cancelled"}:
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
                if order["status"] != "new" or data["kind"] == "order_cancelled":
                    markup = None
                prefix = (
                    "❌ Клиент отменил заявку.\n\n" if data["kind"] == "order_cancelled" else ""
                )
                await bot.send_message(
                    row["chat_id"], prefix + order_text(order, catalog), reply_markup=markup
                )
            elif data["kind"] == "photo":
                await bot.send_photo(row["chat_id"], data["photo"], caption=data["caption"])
            elif data["kind"] == "text":
                key = StorageKey(bot_id=bot.id, chat_id=row["chat_id"], user_id=row["chat_id"])
                async with isolation.lock(key) if isolation else nullcontext():
                    await deliver_receipt(bot, db, row["chat_id"], data)
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


async def deliver_receipt(bot: Bot, db: Database, chat_id: int, payload: dict):
    target = payload.get("screen")
    if target:
        state = FSMContext(
            storage=PostgresStorage(db),
            key=StorageKey(bot_id=bot.id, chat_id=chat_id, user_id=chat_id),
        )
        current = (await state.get_data()).get(SCREEN_KEY, {})
        if (
            current.get("message_id") == target["message_id"]
            and current.get("nonce") == target["nonce"]
            and await state.get_state() in {None, Form.confirm.state}
        ):
            message = Message(
                message_id=target["message_id"],
                date=datetime.now(timezone.utc),
                chat=Chat(id=chat_id, type="private"),
            ).as_(bot)
            await show_screen(message, state, payload["text"], reply_markup=MENU)
            return
    # A receipt arriving after a new flow must not overwrite the new draft's screen.
    await bot.send_message(chat_id, payload["text"], reply_markup=MENU)
