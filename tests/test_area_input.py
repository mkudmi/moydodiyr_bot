from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import DeleteMessage, EditMessageCaption

from moydodyr.bot.handlers import orders
from moydodyr.bot.services.screen import SCREEN_KEY
from moydodyr.bot.states import Form
from tests.test_welcome import load_catalog


@pytest.mark.parametrize("valid", [True, False])
async def test_area_message_is_deleted_after_result_is_shown(screen_env, valid):
    _, session, state, message = screen_env
    await state.update_data(**{SCREEN_KEY: {"message_id": 100, "photo": True}})
    await state.set_state(Form.area)
    db = SimpleNamespace(catalog=AsyncMock(return_value=load_catalog()))
    await orders.choose_area(
        message.model_copy(update={"text": "65,5" if valid else "ошибка"}), state, db
    )
    deletions = [call for call in session.calls if isinstance(call, DeleteMessage)]
    if valid:
        assert (await state.get_data())["area"] == "65.5"
        assert await state.get_state() == Form.extras.state
        assert len(deletions) == 1
        assert deletions[0].message_id == message.message_id
        assert isinstance(session.calls[-2], EditMessageCaption)
        assert isinstance(session.calls[-1], DeleteMessage)
    else:
        assert "area" not in await state.get_data()
        assert await state.get_state() == Form.area.state
        assert len(deletions) == 1
        assert deletions[0].message_id == message.message_id
        assert isinstance(session.calls[-2], EditMessageCaption)
        assert session.calls[-2].caption.startswith("❗ ")
        assert "сообщением в этот чат" in session.calls[-2].caption


async def test_delete_failure_does_not_interrupt_order(screen_env, monkeypatch):
    _, session, state, message = screen_env
    await state.update_data(**{SCREEN_KEY: {"message_id": 100, "photo": True}})
    original_request = session.make_request

    async def request(bot, method, timeout=None):
        if isinstance(method, DeleteMessage):
            raise TelegramForbiddenError(method=method, message="Forbidden")
        return await original_request(bot, method, timeout)

    monkeypatch.setattr(session, "make_request", request)
    await orders.choose_area(
        message.model_copy(update={"text": "65"}),
        state,
        SimpleNamespace(catalog=AsyncMock(return_value=load_catalog())),
    )
    assert (await state.get_data())["area"] == "65"
    assert await state.get_state() == Form.extras.state
