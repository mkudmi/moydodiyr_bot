from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message,
)

from moydodyr.bot.keyboards import (
    MENU,
)

router = Router()
router.message.filter(F.chat.type == "private")


@router.message()
async def fallback(message: Message, state: FSMContext):
    if await state.get_state() is None:
        await message.answer("Выберите действие в меню или отправьте /start.", reply_markup=MENU)
