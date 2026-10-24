from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message,
)

from moydodyr.bot.keyboards import (
    MENU,
)
from moydodyr.bot.services.screen import show_screen

router = Router()
router.message.filter(F.chat.type == "private")


@router.message()
async def fallback(message: Message, state: FSMContext):
    if await state.get_state() is None:
        await show_screen(
            message, state, "Выберите действие в меню или отправьте /start.", reply_markup=MENU
        )
