import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moydodyr.bot.handlers.admin import admin_command
from moydodyr.bot.handlers.menu import WELCOME_PHOTO, start
from moydodyr.domain import validate_catalog


def load_catalog():
    return json.loads((WELCOME_PHOTO.parents[1] / "catalog.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "welcome", [load_catalog()["welcome"], "Я" * 1025, "🫧" * 513], ids=["default", "long", "emoji"]
)
async def test_start_photo_and_long_welcome(screen_env, welcome):
    from aiogram.methods import SendMessage, SendPhoto

    from moydodyr.bot.services.screen import SCREEN_KEY

    _, session, state, message = screen_env
    db = SimpleNamespace(
        register_user=AsyncMock(), catalog=AsyncMock(return_value={"welcome": welcome})
    )

    await start(message, state, db)

    db.register_user.assert_awaited_once_with(42, "campaign")
    assert WELCOME_PHOTO.is_file()
    assert sum(isinstance(call, SendPhoto) for call in session.calls) == 1
    assert not any(isinstance(call, SendMessage) for call in session.calls)
    screen = (await state.get_data())[SCREEN_KEY]
    assert "".join(screen["pages"]) == welcome
    assert all(len(page.encode("utf-16-le")) // 2 <= 1024 for page in screen["pages"])


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
