from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.methods import EditMessageCaption, EditMessageMedia, SendMessage, SendPhoto
from aiogram.types import CallbackQuery

from moydodyr.bot.handlers.menu import cancel, menu_action, start
from moydodyr.bot.keyboards import MENU
from moydodyr.bot.services.screen import SCREEN_IMAGES, SCREEN_KEY, go_back
from moydodyr.bot.states import Form
from tests.test_welcome import load_catalog


@pytest.mark.parametrize(
    "action",
    [button.callback_data for row in MENU.inline_keyboard for button in row] + ["menu:home"],
)
async def test_inline_menu_edits_photo_without_sending_message(screen_env, action):
    bot, session, state, message = screen_env
    db = SimpleNamespace(register_user=AsyncMock(), catalog=AsyncMock(return_value=load_catalog()))
    await start(message, state, db)
    photo = (await state.get_data())[SCREEN_KEY]
    query = CallbackQuery(
        id="click",
        from_user=message.from_user,
        chat_instance="chat",
        data=action,
        message=message.model_copy(update={"message_id": photo["message_id"]}),
    ).as_(bot)
    session.calls.clear()

    await menu_action(query, state, db)

    assert not any(isinstance(call, (SendMessage, SendPhoto)) for call in session.calls)
    edit = next(
        call for call in session.calls if isinstance(call, (EditMessageCaption, EditMessageMedia))
    )
    assert edit.message_id == photo["message_id"]
    labels = [button.text for row in edit.reply_markup.inline_keyboard for button in row]
    assert "↩️ Отмена" not in labels
    if action in {"menu:estimate", "menu:order"}:
        assert await state.get_state() == Form.object.state
        assert (await state.get_data())["mode"] == action.split(":")[1]
        assert "Квартира" in str(edit.reply_markup)
        assert labels.count("↩️ Главное меню") == 1
    elif action == "menu:contact":
        assert await state.get_state() == Form.manager_message.state
    else:
        assert await state.get_state() is None


async def test_cancel_preserves_screen_and_clears_draft(screen_env):
    _, session, state, message = screen_env
    await state.update_data(**{SCREEN_KEY: {"message_id": 100, "photo": True}, "draft_id": "draft"})
    await state.set_state(Form.area)
    await cancel(message, state)
    assert await state.get_state() is None
    assert "draft_id" not in await state.get_data()
    assert len(session.calls) == 1
    assert isinstance(session.calls[0], EditMessageCaption)


async def test_estimate_picture_is_kept_on_steps_and_restored_on_home(screen_env):
    from moydodyr.bot.handlers import orders

    bot, session, state, message = screen_env
    db = SimpleNamespace(register_user=AsyncMock(), catalog=AsyncMock(return_value=load_catalog()))
    await start(message, state, db)
    query = CallbackQuery(
        id="image",
        from_user=message.from_user,
        chat_instance="chat",
        data="menu:estimate",
        message=message.model_copy(update={"message_id": 100}),
    ).as_(bot)
    session.calls.clear()
    await menu_action(query, state, db)
    edits = [call for call in session.calls if isinstance(call, EditMessageMedia)]
    assert len(edits) == 1
    assert edits[0].media.media.path == SCREEN_IMAGES["estimate"]
    assert SCREEN_IMAGES["estimate"].is_file()
    await orders.choose_object(message.model_copy(update={"text": "Квартира"}), state, db)
    await go_back(message, state)
    assert (await state.get_data())[SCREEN_KEY]["displayed_image"] == "estimate"
    assert sum(isinstance(call, EditMessageMedia) for call in session.calls) == 1
    await menu_action(query.model_copy(update={"data": "menu:home"}), state, db)
    edits = [call for call in session.calls if isinstance(call, EditMessageMedia)]
    assert len(edits) == 2
    assert edits[-1].media.media.path == SCREEN_IMAGES["welcome"]
    assert all(call.message_id == 100 for call in edits)
    assert not any(isinstance(call, (SendMessage, SendPhoto)) for call in session.calls)
