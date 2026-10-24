from datetime import datetime, timezone

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import SendMessage, SendPhoto
from aiogram.types import Chat, Message, PhotoSize, User


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        yield b""

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, (SendMessage, SendPhoto)):
            return Message(
                message_id=100,
                date=datetime.now(timezone.utc),
                chat=Chat(id=42, type="private"),
                from_user=User(id=bot.id, is_bot=True, first_name="Bot"),
                photo=[PhotoSize(file_id="photo", file_unique_id="photo", width=100, height=100)]
                if isinstance(method, SendPhoto)
                else None,
            ).as_(bot)
        return True


@pytest.fixture
def screen_env():
    session = FakeSession()
    bot = Bot("123456:TEST_TOKEN_FOR_OFFLINE_TESTS", session=session)
    state = FSMContext(MemoryStorage(), StorageKey(bot_id=bot.id, chat_id=42, user_id=42))
    message = Message(
        message_id=10,
        date=datetime.now(timezone.utc),
        chat=Chat(id=42, type="private"),
        from_user=User(id=42, is_bot=False, first_name="Клиент", username="client"),
        text="/start campaign",
    ).as_(bot)
    return bot, session, state, message
