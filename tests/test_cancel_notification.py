import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.methods import SendMessage

from moydodyr.bot.services.outbox import deliver_outbox
from tests.test_account import sample_order
from tests.test_welcome import load_catalog


async def test_manager_receives_cancelled_card_without_take_button(screen_env):
    bot, session, _, _ = screen_env
    order = {**sample_order(), "status": "cancelled", "manager_id": None, "source": "Тест"}
    db = SimpleNamespace(
        next_notification=AsyncMock(
            side_effect=[
                {"id": 1, "chat_id": -123, "payload": {"kind": "order_cancelled", "order_id": 7}},
                asyncio.CancelledError(),
            ]
        ),
        order=AsyncMock(return_value=order),
        catalog=AsyncMock(return_value=load_catalog()),
        mark_delivered=AsyncMock(),
    )
    with pytest.raises(asyncio.CancelledError):
        await deliver_outbox(bot, db, None)
    call = session.calls[-1]
    assert isinstance(call, SendMessage)
    assert call.chat_id == -123
    assert "Клиент отменил заявку" in call.text
    assert "Статус: Отменена" in call.text
    assert call.reply_markup is None
    db.mark_delivered.assert_awaited_once_with(1)
