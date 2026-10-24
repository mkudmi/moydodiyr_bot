import asyncio

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from moydodyr.bot.services.photos import (
    ALBUM_GUARD,
    ALBUM_TASKS,
    PENDING_ALBUMS,
    append_photos,
    flush_album,
)
from moydodyr.bot.states import Form
from moydodyr.db import Database

photos_router = Router()
photos_router.message.filter(F.chat.type == "private")


@photos_router.message(Form.photos, F.photo)
async def add_photo(message: Message, state: FSMContext, db: Database):
    if not message.media_group_id:
        await append_photos([message], state, db)
        return
    key = (message.chat.id, message.from_user.id, message.media_group_id)
    async with ALBUM_GUARD:
        PENDING_ALBUMS.setdefault(key, []).append(message)
        previous_task = ALBUM_TASKS.get(key)
        if previous_task is None or previous_task.done():
            ALBUM_TASKS[key] = asyncio.create_task(flush_album(key, state, db))
