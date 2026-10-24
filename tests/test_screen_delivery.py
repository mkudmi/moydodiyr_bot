from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram import Dispatcher
from aiogram.methods import EditMessageCaption, SendMessage
from aiogram.types import CallbackQuery, Update

from moydodyr.bot.handlers.customer import router
from moydodyr.bot.services import outbox
from moydodyr.bot.services.screen import SCREEN_KEY, show_screen
from moydodyr.bot.states import Form
from tests.test_welcome import load_catalog


async def test_dispatcher_routes_object_callback_to_customer_fsm(screen_env):
    bot, session, state, message = screen_env
    dispatcher = Dispatcher(storage=state.storage)
    dispatcher.include_router(router)
    db = SimpleNamespace(register_user=AsyncMock(), catalog=AsyncMock(return_value=load_catalog()))
    settings = SimpleNamespace(webhook_enabled=False, manager_chat_id=-123)
    await dispatcher.feed_update(
        bot, Update(update_id=1, message=message), db=db, settings=settings
    )

    async def click(data, update_id):
        query = CallbackQuery(
            id=str(update_id),
            from_user=message.from_user,
            chat_instance="chat",
            data=data,
            message=message.model_copy(update={"message_id": 100}),
        )
        await dispatcher.feed_update(
            bot,
            Update(update_id=update_id, callback_query=query),
            db=db,
            settings=settings,
        )

    await click("menu:estimate", 2)
    screen = (await state.get_data())[SCREEN_KEY]
    session.calls.clear()
    await click(f"screen:pick:{screen['nonce']}:0", 3)
    assert await state.get_state() == Form.service.state
    assert (await state.get_data())["object"] == "flat"
    assert not any(isinstance(call, SendMessage) for call in session.calls)
    assert any(isinstance(call, EditMessageCaption) for call in session.calls)
    db.customer_orders = AsyncMock(return_value=[])
    await click("menu:my_orders", 4)
    db.customer_orders.assert_awaited_once_with(42, active=True, offset=0)
    assert await state.get_state() is None


async def test_receipt_edits_screen_but_does_not_replace_new_flow(screen_env, monkeypatch):
    bot, session, state, message = screen_env
    await state.update_data(**{SCREEN_KEY: {"message_id": 100, "photo": True}})
    await show_screen(message, state, "Проверьте заявку")
    screen = (await state.get_data())[SCREEN_KEY]
    payload = {
        "text": "Заявка принята",
        "screen": {
            "message_id": 100,
            "nonce": screen["nonce"],
        },
    }
    monkeypatch.setattr(outbox, "PostgresStorage", lambda db: state.storage)
    session.calls.clear()
    await outbox.deliver_receipt(bot, None, 42, payload)
    assert len(session.calls) == 1
    assert isinstance(session.calls[0], EditMessageCaption)
    assert session.calls[0].caption == "Заявка принята"

    await state.set_state(Form.area)
    await show_screen(message, state, "Укажите площадь")
    session.calls.clear()
    await outbox.deliver_receipt(bot, None, 42, payload)
    assert len(session.calls) == 1
    assert isinstance(session.calls[0], SendMessage)
    assert (await state.get_data())[SCREEN_KEY]["pages"] == ["Укажите площадь"]
