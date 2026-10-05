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

photos_router = Router()
photos_router.message.filter(F.chat.type == "private")


@photos_router.message(Form.photos, F.photo)
async def add_photo(message: Message, state: FSMContext):
    if not message.media_group_id:
        await append_photos([message], state)
        return
    key = (message.chat.id, message.from_user.id, message.media_group_id)
    async with ALBUM_GUARD:
        PENDING_ALBUMS.setdefault(key, []).append(message)
        previous_task = ALBUM_TASKS.get(key)
        if previous_task and not previous_task.done():
            previous_task.cancel()
        ALBUM_TASKS[key] = asyncio.create_task(flush_album(key, state))
