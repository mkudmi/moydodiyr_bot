import asyncio

from aiogram.fsm.context import FSMContext
from aiogram.types import Message

PENDING_ALBUMS: dict[tuple[int, int, str], list[Message]] = {}


ALBUM_TASKS: dict[tuple[int, int, str], asyncio.Task] = {}


ALBUM_GUARD = asyncio.Lock()


PHOTO_LOCKS: dict[tuple[int, int], asyncio.Lock] = {}


def photo_lock(user_key: tuple[int, int]) -> asyncio.Lock:
    return PHOTO_LOCKS.setdefault(user_key, asyncio.Lock())


async def append_photos(messages: list[Message], state: FSMContext):
    if not messages:
        return
    user_key = (messages[0].chat.id, messages[0].from_user.id)
    async with photo_lock(user_key):
        data = await state.get_data()
        existing = list(data.get("photos", []))
        remaining = max(0, 5 - len(existing))
        incoming = [message.photo[-1].file_id for message in messages if message.photo]
        added = incoming[:remaining]
        photos = existing + added
        await state.update_data(photos=photos)
        note = f"Добавлено фото: {len(photos)}/5. Пришлите ещё или нажмите «Готово»."
        if len(incoming) > remaining:
            note = f"В заявке максимум 5 фото. Сохранено {len(photos)}. Нажмите «Готово»."
        await messages[-1].answer(note)


async def flush_album(key: tuple[int, int, str], state: FSMContext):
    await asyncio.sleep(0.8)
    user_key = key[:2]
    async with photo_lock(user_key):
        async with ALBUM_GUARD:
            messages = PENDING_ALBUMS.pop(key, [])
            ALBUM_TASKS.pop(key, None)
        if messages:
            await append_photos_unlocked(messages, state)


async def append_photos_unlocked(messages: list[Message], state: FSMContext):
    data = await state.get_data()
    existing = list(data.get("photos", []))
    incoming = [message.photo[-1].file_id for message in messages if message.photo]
    added = incoming[: max(0, 5 - len(existing))]
    photos = existing + added
    await state.update_data(photos=photos)
    if len(incoming) > len(added):
        note = f"В заявке максимум 5 фото. Сохранено {len(photos)}. Нажмите «Готово»."
    else:
        note = f"Добавлено {len(added)} фото ({len(photos)}/5). Нажмите «Готово», когда закончите."
    await messages[-1].answer(note)


async def flush_user_albums(message: Message, state: FSMContext):
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
            await append_photos_unlocked(messages, state)
