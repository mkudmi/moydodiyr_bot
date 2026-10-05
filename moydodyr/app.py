import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from moydodyr.bot.handlers.customer import router as customer_router
from moydodyr.bot.handlers.group import group_router
from moydodyr.bot.handlers.photos import photos_router
from moydodyr.bot.services.outbox import deliver_outbox
from moydodyr.config import Settings
from moydodyr.db import Database, PostgresStorage


async def run():
    logging.basicConfig(level=logging.INFO)
    settings = Settings.load()
    db = await Database.connect(settings.database_url)
    moved = await db.retarget_pending_manager_notifications(settings.manager_chat_id)
    if moved:
        logging.info("Retargeted %s pending manager notifications", moved)
    bot = Bot(settings.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=PostgresStorage(db))
    dp.include_router(group_router)
    dp.include_router(photos_router)
    dp.include_router(customer_router)
    try:
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(deliver_outbox(bot, db, settings))
            tasks.create_task(
                dp.start_polling(
                    bot, db=db, settings=settings, allowed_updates=dp.resolve_used_update_types()
                )
            )
    finally:
        await bot.session.close()
        await db.pool.close()
