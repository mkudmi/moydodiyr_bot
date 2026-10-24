from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.methods import EditMessageCaption, SendMessage, SendPhoto
from aiogram.types import CallbackQuery

from moydodyr.bot.handlers import orders
from moydodyr.bot.handlers.menu import menu_action, start
from moydodyr.bot.handlers.screen import screen_action
from moydodyr.bot.services.screen import SCREEN_KEY, keyboard_for, show_screen
from moydodyr.bot.states import Form
from moydodyr.domain import MOSCOW
from tests.test_welcome import load_catalog


async def test_full_order_edits_one_photo_and_uses_clicking_customer(screen_env):
    bot, session, state, user_message = screen_env
    db = SimpleNamespace(
        register_user=AsyncMock(),
        catalog=AsyncMock(return_value=load_catalog()),
        submit=AsyncMock(),
    )
    settings = SimpleNamespace(webhook_enabled=True, manager_chat_id=-123)
    await start(user_message, state, db)
    session.calls.clear()

    async def click(data):
        message = user_message.model_copy(update={"message_id": 100})
        query = CallbackQuery(
            id="test",
            from_user=user_message.from_user,
            chat_instance="chat",
            message=message,
            data=data,
        ).as_(bot)
        if data.startswith("menu:"):
            await menu_action(query, state, db)
        elif data.startswith("cal:"):
            await orders.calendar_callback(query, state)
        elif data.startswith("tm:"):
            await orders.time_callback(query, state)
        else:
            await screen_action(query, state, db, settings)

    async def pick(label):
        screen = (await state.get_data())[SCREEN_KEY]
        markup = keyboard_for(screen, 0)
        button = next(b for row in markup.inline_keyboard for b in row if b.text == label)
        await click(button.callback_data)
        return button.callback_data

    async def type_text(handler, text, **kwargs):
        await handler(user_message.model_copy(update={"text": text}), state, **kwargs)

    await click("menu:order")
    old_button = await pick("Квартира")
    assert await state.get_state() == Form.service.state
    await click(old_button)
    assert await state.get_state() == Form.service.state
    await pick("Генеральная уборка")
    await type_text(orders.choose_area, "65,5", db=db)
    await pick("▫️ Мойка окон (за створку)")
    assert (await state.get_data())["extras"] == ["windows"]
    await pick("Готово")
    await type_text(orders.address_text, "Москва, улица Тестовая, дом 1")
    tomorrow = (datetime.now(MOSCOW).date() + timedelta(days=1)).isoformat()
    await click(f"cal:select:{tomorrow}")
    await click("tm:any")
    await type_text(orders.choose_name, "Иван <Тест>")
    await type_text(orders.choose_phone, "89991234567")
    await pick("Пропустить")
    await pick("Пропустить")
    assert await state.get_state() == Form.confirm.state
    await pick("✅ Подтвердить заявку")

    db.submit.assert_awaited_once()
    assert db.submit.call_args.args[0] == 42
    draft = db.submit.call_args.args[1]
    assert draft["name"] == "Иван <Тест>"
    assert draft["telegram_user"] == 42
    assert SCREEN_KEY not in draft
    assert db.submit.call_args.kwargs["webhook_payload"]["telegram"] == "@client"
    assert db.submit.call_args.kwargs["receipt_screen"]["message_id"] == 100
    assert await state.get_state() is None
    assert not any(isinstance(call, (SendMessage, SendPhoto)) for call in session.calls)
    edits = [call for call in session.calls if isinstance(call, EditMessageCaption)]
    assert len(edits) > 10
    assert all(call.message_id == 100 for call in edits)
    assert any("Иван <Тест>" in call.caption for call in edits)


async def test_long_caption_paginates_without_sending_another_message(screen_env):
    bot, session, state, message = screen_env
    await state.update_data(**{SCREEN_KEY: {"message_id": 100, "photo": True}})
    text = "Длинный текст 🫧 " * 200
    await show_screen(message, state, text)
    screen = (await state.get_data())[SCREEN_KEY]
    assert "".join(screen["pages"]) == text
    button = next(
        b
        for row in keyboard_for(screen, 0).inline_keyboard
        for b in row
        if b.callback_data.startswith("screen:page:")
    )
    query = CallbackQuery(
        id="page",
        from_user=message.from_user,
        chat_instance="chat",
        message=message.model_copy(update={"message_id": 100}),
        data=button.callback_data,
    ).as_(bot)
    await screen_action(query, state, None, None)
    assert all(
        isinstance(call, EditMessageCaption) for call in session.calls if hasattr(call, "caption")
    )
    assert session.calls[-1].caption == screen["pages"][1]
