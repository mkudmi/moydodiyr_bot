from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.methods import SendMessage, SendPhoto

from moydodyr.bot.handlers.miniapp_entry import old_button, welcome
from moydodyr.bot.services.outbox import deliver_receipt


@pytest.mark.asyncio
async def test_welcome_has_one_application_button(screen_env):
    bot, session, state, message = screen_env
    db = SimpleNamespace(register_user=AsyncMock())
    settings = SimpleNamespace(mini_app_url="https://example.test/")
    await welcome(message, state, db, settings)
    assert len(session.calls) == 1
    sent = session.calls[0]
    assert isinstance(sent, SendPhoto)
    assert len(sent.reply_markup.inline_keyboard) == 1
    assert len(sent.reply_markup.inline_keyboard[0]) == 1
    assert sent.reply_markup.inline_keyboard[0][0].web_app.url == settings.mini_app_url
    assert "меню" not in sent.caption.lower()
    db.register_user.assert_awaited_once_with(42, "campaign")


@pytest.mark.asyncio
async def test_welcome_without_url_explains_app_is_unavailable(screen_env):
    _, session, state, message = screen_env
    await welcome(
        message,
        state,
        SimpleNamespace(register_user=AsyncMock()),
        SimpleNamespace(mini_app_url=None),
    )
    assert len(session.calls) == 1
    assert "пока недоступно" in session.calls[0].caption
    assert session.calls[0].reply_markup is None


@pytest.mark.asyncio
async def test_receipt_uses_only_passed_keyboard(screen_env):
    bot, session, _, _ = screen_env
    await deliver_receipt(bot, None, 42, {"text": "Заявка принята"}, None)
    assert isinstance(session.calls[0], SendMessage)
    assert session.calls[0].reply_markup is None


@pytest.mark.asyncio
async def test_old_buttons_no_longer_open_a_flow():
    query = SimpleNamespace(answer=AsyncMock())
    await old_button(query)
    query.answer.assert_awaited_once_with(
        "Это меню больше не используется. Отправьте /start.", show_alert=True
    )
