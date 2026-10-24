from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from aiogram.methods import DeleteMessage
from aiogram.types import PhotoSize

from moydodyr.bot.services.photo_archive import PhotoBuffer, archive_telegram_photo, image_type
from moydodyr.bot.services.photos import append_photos
from moydodyr.bot.services.screen import SCREEN_KEY
from moydodyr.bot.states import Form


async def test_archive_downloads_actual_bytes_and_passes_metadata_to_database(screen_env):
    bot, _, _, _ = screen_env
    db = SimpleNamespace(draft_photo=AsyncMock(return_value=None), archive_photo=AsyncMock())
    draft_id = str(uuid4())
    photo = PhotoSize(file_id="file", file_unique_id="unique", width=500, height=400)
    await archive_telegram_photo(bot, db, 42, draft_id, "file", photo)
    args = db.archive_photo.call_args
    assert args.args == (42, draft_id, "file", b"\xff\xd8\xfftest-image", "image/jpeg")
    assert args.kwargs == {"unique_id": "unique", "width": 500, "height": 400}


async def test_archive_reuses_saved_file_without_calling_telegram(screen_env):
    bot, session, _, _ = screen_env
    db = SimpleNamespace(
        draft_photo=AsyncMock(return_value={"id": "saved"}), archive_photo=AsyncMock()
    )
    assert await archive_telegram_photo(bot, db, 42, str(uuid4()), "file") == {"id": "saved"}
    assert session.calls == []
    db.archive_photo.assert_not_awaited()


async def test_archive_failure_preserves_user_photo_and_draft(screen_env):
    _, session, state, message = screen_env
    await state.set_state(Form.photos)
    await state.update_data(
        **{
            SCREEN_KEY: {"message_id": 100, "photo": True},
            "photos": [],
            "draft_id": str(uuid4()),
        }
    )
    db = SimpleNamespace(
        draft_photo=AsyncMock(return_value=None),
        archive_photo=AsyncMock(side_effect=RuntimeError("storage unavailable")),
    )
    message = message.model_copy(
        update={
            "photo": [PhotoSize(file_id="file", file_unique_id="unique", width=500, height=400)]
        }
    )
    await append_photos([message], state, db)
    assert (await state.get_data())["photos"] == []
    assert not any(isinstance(call, DeleteMessage) for call in session.calls)
    assert "❗" in session.calls[-1].caption


def test_photo_format_and_memory_limit():
    assert image_type(b"\xff\xd8\xffabc") == "image/jpeg"
    assert image_type(b"\x89PNG\r\n\x1a\nabc") == "image/png"
    with pytest.raises(ValueError):
        image_type(b"not a photo")
    buffer = PhotoBuffer()
    buffer.seek(20 * 1024 * 1024)
    with pytest.raises(ValueError):
        buffer.write(b"x")


async def test_incomplete_download_is_not_saved(screen_env, monkeypatch):
    bot, session, _, _ = screen_env

    async def short_stream(*args, **kwargs):
        yield b"\xff\xd8\xff"

    monkeypatch.setattr(session, "stream_content", short_stream)
    db = SimpleNamespace(draft_photo=AsyncMock(return_value=None), archive_photo=AsyncMock())
    with pytest.raises(ValueError, match="Incomplete"):
        await archive_telegram_photo(bot, db, 42, str(uuid4()), "file")
    db.archive_photo.assert_not_awaited()
