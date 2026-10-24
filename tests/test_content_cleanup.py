from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from aiogram.methods import DeleteMessage, EditMessageCaption
from aiogram.types import PhotoSize

from moydodyr.bot.handlers.orders import choose_comment
from moydodyr.bot.services.photos import PENDING_ALBUMS, append_photos, flush_user_albums
from moydodyr.bot.services.screen import SCREEN_KEY
from moydodyr.bot.states import Form


@pytest.mark.parametrize("inline_skip", [False, True])
async def test_comment_saved_and_deleted_without_deleting_bot_screen(screen_env, inline_skip):
    _, session, state, message = screen_env
    await state.update_data(**{SCREEN_KEY: {"message_id": 100, "photo": True}})
    await state.set_state(Form.comment)
    message = message.model_copy(
        update={
            "message_id": 100 if inline_skip else 10,
            "text": "Пропустить" if inline_skip else "Позвоните заранее",
        }
    )
    await choose_comment(message, state)
    assert (await state.get_data())["comment"] == ("" if inline_skip else "Позвоните заранее")
    assert await state.get_state() == Form.photos.state
    deletions = [call for call in session.calls if isinstance(call, DeleteMessage)]
    assert [call.message_id for call in deletions] == ([] if inline_skip else [10])


@pytest.mark.parametrize("kind,count", [("single", 1), ("album", 3), ("album", 7)])
async def test_photos_deleted_only_after_file_ids_are_saved(screen_env, kind, count):
    _, session, state, message = screen_env
    await state.update_data(
        **{SCREEN_KEY: {"message_id": 100, "photo": True}, "photos": [], "draft_id": str(uuid4())}
    )
    db = SimpleNamespace(
        draft_photo=AsyncMock(return_value=None),
        archive_photo=AsyncMock(return_value={"id": str(uuid4())}),
    )
    await state.set_state(Form.photos)
    messages = [
        message.model_copy(
            update={
                "message_id": 10 + i,
                "text": None,
                "photo": [
                    PhotoSize(
                        file_id=f"file{i}", file_unique_id=f"unique{i}", width=500, height=500
                    )
                ],
                "media_group_id": "album" if kind == "album" else None,
            }
        )
        for i in range(count)
    ]
    if kind == "single":
        await append_photos(messages, state, db)
    else:
        PENDING_ALBUMS[(42, 42, "album")] = messages
        await flush_user_albums(message, state, db)
    saved_count = min(count, 5)
    assert (await state.get_data())["photos"] == [f"file{i}" for i in range(saved_count)]
    assert any(isinstance(call, EditMessageCaption) for call in session.calls)
    assert db.archive_photo.await_count == saved_count
    deletions = [call.message_id for call in session.calls if isinstance(call, DeleteMessage)]
    assert deletions == list(range(10, 10 + saved_count))
