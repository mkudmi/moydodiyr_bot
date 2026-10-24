import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import ReplyKeyboardRemove

from moydodyr.bot.handlers.admin import admin_command
from moydodyr.bot.handlers.menu import WELCOME_PHOTO, start
from moydodyr.bot.keyboards import MENU
from moydodyr.domain import validate_catalog


def load_catalog():
    return json.loads((WELCOME_PHOTO.parents[1] / "catalog.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("welcome", [load_catalog()["welcome"], "Я" * 1025, "🫧" * 513])
async def test_start_photo_and_long_welcome(welcome):
    message = SimpleNamespace(
        text="/start campaign",
        from_user=SimpleNamespace(id=42),
        answer=AsyncMock(),
        answer_photo=AsyncMock(),
    )
    state = SimpleNamespace(clear=AsyncMock())
    db = SimpleNamespace(
        register_user=AsyncMock(), catalog=AsyncMock(return_value={"welcome": welcome})
    )

    await start(message, state, db)

    state.clear.assert_awaited_once()
    db.register_user.assert_awaited_once_with(42, "campaign")
    assert WELCOME_PHOTO.is_file()
    assert message.answer_photo.call_args.args[0].path == WELCOME_PHOTO
    if len(welcome.encode("utf-16-le")) // 2 <= 1024:
        assert message.answer_photo.call_args.kwargs == {"caption": welcome, "reply_markup": MENU}
        message.answer.assert_awaited_once_with(
            "🫧 Наведём порядок вместе!", reply_markup=ReplyKeyboardRemove()
        )
    else:
        assert message.answer_photo.call_args.kwargs == {}
        message.answer.assert_any_await(welcome, reply_markup=MENU)
        assert message.answer.await_count == 2


async def test_admin_updates_multiline_welcome_without_changing_catalog():
    catalog = load_catalog()
    original = deepcopy(catalog)
    welcome = "Привет! Рады видеть вас.\n\nВыберите уборку и рассчитайте стоимость."
    message = SimpleNamespace(
        text=f"/admin text welcome {welcome}",
        from_user=SimpleNamespace(id=42),
        answer=AsyncMock(),
    )

    async def change_catalog(change):
        change(catalog)
        validate_catalog(catalog)

    db = SimpleNamespace(catalog=AsyncMock(return_value=catalog), change_catalog=change_catalog)
    await admin_command(message, db, SimpleNamespace(admin_ids={42}))

    assert catalog == {**original, "welcome": welcome}
    message.answer.assert_awaited_once_with("Настройки сохранены.")
