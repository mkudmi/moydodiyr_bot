from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Message, ReplyKeyboardMarkup, ReplyKeyboardRemove

from moydodyr.bot.handlers.menu import cancel, menu_action
from moydodyr.bot.keyboards import MENU
from moydodyr.bot.states import Form
from tests.test_welcome import load_catalog


@pytest.mark.parametrize(
    "action", [button.callback_data for row in MENU.inline_keyboard for button in row]
)
async def test_inline_menu_actions(action):
    message = MagicMock(spec=Message)
    message.answer = AsyncMock()
    query = SimpleNamespace(data=action, message=message, answer=AsyncMock())
    state = SimpleNamespace(clear=AsyncMock(), update_data=AsyncMock(), set_state=AsyncMock())
    db = SimpleNamespace(catalog=AsyncMock(return_value=load_catalog()))

    await menu_action(query, state, db)

    query.answer.assert_awaited_once()
    message.answer.assert_awaited_once()
    markup = message.answer.call_args.kwargs["reply_markup"]
    if action in {"menu:estimate", "menu:order"}:
        state.clear.assert_awaited_once()
        state.set_state.assert_awaited_once_with(Form.object)
        assert state.update_data.call_args.kwargs["mode"] == action.split(":")[1]
        assert isinstance(markup, ReplyKeyboardMarkup)
    elif action == "menu:contact":
        state.clear.assert_awaited_once()
        state.set_state.assert_awaited_once_with(Form.manager_message)
        assert markup.keyboard[0][0].request_contact
    else:
        assert markup == MENU
        state.clear.assert_not_awaited()
        if action == "menu:services":
            assert "Генеральная уборка" in message.answer.call_args.args[0]
        else:
            assert message.answer.call_args.args[0] == load_catalog()[action.split(":")[1]]


async def test_cancel_removes_form_keyboard_and_attaches_menu():
    message = SimpleNamespace(answer=AsyncMock())
    state = SimpleNamespace(clear=AsyncMock())
    await cancel(message, state)
    state.clear.assert_awaited_once()
    assert isinstance(message.answer.call_args_list[0].kwargs["reply_markup"], ReplyKeyboardRemove)
    assert message.answer.call_args_list[1].kwargs["reply_markup"] == MENU
