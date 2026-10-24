import asyncio
import logging

from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from moydodyr.bot.services.messages import delete_saved_input
from moydodyr.bot.services.photo_archive import archive_telegram_photo
from moydodyr.bot.services.screen import show_screen
from moydodyr.bot.states import Form
from moydodyr.db import Database

PENDING_ALBUMS: dict[tuple[int, int, str], list[Message]] = {}


ALBUM_TASKS: dict[tuple[int, int, str], asyncio.Task] = {}


ALBUM_GUARD = asyncio.Lock()


PHOTO_LOCKS: dict[tuple[int, int], asyncio.Lock] = {}


def photo_lock(user_key: tuple[int, int]) -> asyncio.Lock:
    return PHOTO_LOCKS.setdefault(user_key, asyncio.Lock())


async def append_photos(messages: list[Message], state: FSMContext, db: Database):
    if not messages:
        return
    user_key = (messages[0].chat.id, messages[0].from_user.id)
    async with photo_lock(user_key):
        await append_photos_unlocked(messages, state, db)


async def flush_album(key: tuple[int, int, str], state: FSMContext, db: Database):
    try:
        while True:
            await asyncio.sleep(0.8)
            async with photo_lock(key[:2]):
                async with ALBUM_GUARD:
                    messages = PENDING_ALBUMS.pop(key, [])
                if messages:
                    await append_photos_unlocked(messages, state, db)
            async with ALBUM_GUARD:
                if key not in PENDING_ALBUMS:
                    ALBUM_TASKS.pop(key, None)
                    return
    finally:
        async with ALBUM_GUARD:
            if ALBUM_TASKS.get(key) is asyncio.current_task():
                ALBUM_TASKS.pop(key, None)


async def append_photos_unlocked(messages: list[Message], state: FSMContext, db: Database):
    if await state.get_state() != Form.photos.state:
        return
    data = await state.get_data()
    existing = list(data.get("photos", []))
    incoming = [message for message in messages if message.photo]
    remaining = max(0, 5 - len(existing))
    saved, failed = [], 0
    for message in incoming[:remaining]:
        try:
            await archive_telegram_photo(
                message.bot,
                db,
                message.from_user.id,
                data["draft_id"],
                message.photo[-1].file_id,
                message.photo[-1],
            )
            saved.append(message)
        except Exception as error:
            logging.warning("Photo archival failed (%s)", type(error).__name__)
            failed += 1
    latest = await state.get_data()
    if await state.get_state() != Form.photos.state or latest.get("draft_id") != data.get(
        "draft_id"
    ):
        return
    photos = existing + [message.photo[-1].file_id for message in saved]
    await state.update_data(photos=photos)
    note = f"Сохранено фото: {len(photos)}/5. Пришлите ещё или нажмите «Готово»."
    if len(incoming) > remaining:
        note = f"В заявке максимум 5 фото. Сохранено {len(photos)}. Нажмите «Готово»."
    if failed:
        note += (
            f"\n\n❗ Не удалось сохранить {failed} фото. "
            "Они остались в чате — отправьте их повторно."
        )
    await show_screen(messages[-1], state, note)
    for message in saved:
        await delete_saved_input(message)


async def flush_user_albums(message: Message, state: FSMContext, db: Database):
    user_key = (message.chat.id, message.from_user.id)
    async with photo_lock(user_key):
        async with ALBUM_GUARD:
            keys = [key for key in PENDING_ALBUMS if key[:2] == user_key]
            messages = []
            for key in keys:
                messages.extend(PENDING_ALBUMS.pop(key, []))
                task = ALBUM_TASKS.pop(key, None)
                if task and task is not asyncio.current_task():
                    task.cancel()
        if messages:
            await append_photos_unlocked(messages, state, db)
