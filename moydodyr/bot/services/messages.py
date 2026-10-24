import logging

from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message


async def delete_saved_input(message: Message):
    try:
        await message.delete()
    except TelegramAPIError as error:
        logging.warning("Could not delete handled user input (%s)", type(error).__name__)
