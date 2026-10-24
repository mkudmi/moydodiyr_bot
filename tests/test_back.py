from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.methods import EditMessageCaption, SendMessage
from aiogram.types import CallbackQuery

from moydodyr.bot.handlers import orders
from moydodyr.bot.handlers.menu import menu_action
from moydodyr.bot.handlers.screen import screen_action
from moydodyr.bot.services.screen import SCREEN_KEY, go_back, keyboard_for
from moydodyr.bot.states import Form
from tests.test_welcome import load_catalog


async def test_back_restores_object_choice_and_rejects_old_service_buttons(screen_env):
    bot, session, state, message = screen_env
    db = SimpleNamespace(catalog=AsyncMock(return_value=load_catalog()))
    await state.update_data(**{SCREEN_KEY: {"message_id": 100, "photo": True}})
    await orders.begin_form(message, state, db, "order")
    await orders.choose_object(message.model_copy(update={"text": "Квартира"}), state, db)
    current = (await state.get_data())[SCREEN_KEY]
    old_pick = f"screen:pick:{current['nonce']}:0"
    back = next(
        button
        for row in keyboard_for(current, 0).inline_keyboard
        for button in row
        if button.text == "← Назад"
    )
    query = CallbackQuery(
        id="back",
        from_user=message.from_user,
        chat_instance="chat",
        data=back.callback_data,
        message=message.model_copy(update={"message_id": 100}),
    ).as_(bot)
    session.calls.clear()
    await screen_action(query, state, db, None)
    data = await state.get_data()
    assert await state.get_state() == Form.object.state
    assert "object" not in data
    assert "service" not in data
    assert "Квартира" in str(keyboard_for(data[SCREEN_KEY], 0))
    assert len(session.calls) == 2  # Answer callback and edit the existing photo caption.
    assert isinstance(session.calls[-1], EditMessageCaption)
    assert session.calls[-1].message_id == 100
    await screen_action(query.model_copy(update={"data": old_pick}), state, db, None)
    assert await state.get_state() == Form.object.state
    await orders.choose_object(message.model_copy(update={"text": "Дом / коттедж"}), state, db)
    assert (await state.get_data())["object"] == "house"
    assert not any(isinstance(call, SendMessage) for call in session.calls)


async def test_back_skips_validation_errors_and_extra_toggles(screen_env):
    _, _, state, message = screen_env
    db = SimpleNamespace(catalog=AsyncMock(return_value=load_catalog()))
    await state.update_data(**{SCREEN_KEY: {"message_id": 100, "photo": True}})
    await orders.begin_form(message, state, db, "estimate")
    await orders.choose_object(message.model_copy(update={"text": "Квартира"}), state, db)
    await orders.choose_service(
        message.model_copy(update={"text": "Генеральная уборка"}), state, db
    )
    await orders.choose_area(message.model_copy(update={"text": "ошибка"}), state, db)
    assert len((await state.get_data())[SCREEN_KEY]["history"]) == 2
    await orders.choose_area(message.model_copy(update={"text": "65"}), state, db)
    await orders.choose_extras(
        message.model_copy(update={"text": "▫️ Мойка окон (за створку)"}), state, db
    )
    await orders.choose_extras(message.model_copy(update={"text": "Готово"}), state, db)
    assert await state.get_state() == Form.estimate_offer.state
    result_text = "".join((await state.get_data())[SCREEN_KEY]["pages"])
    assert "Площадь: 65 м²." in result_text
    assert "• Мойка окон (за створку)" in result_text
    assert "Химчистка дивана" not in result_text
    await go_back(message, state)
    assert await state.get_state() == Form.extras.state
    assert (await state.get_data())["extras"] == ["windows"]
    assert "estimate" not in await state.get_data()
    await go_back(message, state)
    assert await state.get_state() == Form.area.state
    assert "area" not in await state.get_data()
    await go_back(message, state)
    assert await state.get_state() == Form.service.state
    await go_back(message, state)
    assert await state.get_state() == Form.object.state
    assert (await state.get_data())[SCREEN_KEY]["history"] == []


async def test_main_menu_discards_back_history(screen_env):
    bot, _, state, message = screen_env
    db = SimpleNamespace(catalog=AsyncMock(return_value=load_catalog()))
    await state.update_data(**{SCREEN_KEY: {"message_id": 100, "photo": True}})
    await orders.begin_form(message, state, db, "order")
    await orders.choose_object(message.model_copy(update={"text": "Квартира"}), state, db)
    query = CallbackQuery(
        id="home",
        from_user=message.from_user,
        chat_instance="chat",
        data="menu:home",
        message=message.model_copy(update={"message_id": 100}),
    ).as_(bot)
    await menu_action(query, state, db)
    assert await state.get_state() is None
    assert (await state.get_data())[SCREEN_KEY]["history"] == []
    await orders.begin_form(message, state, db, "estimate")
    assert (await state.get_data())[SCREEN_KEY]["history"] == []
