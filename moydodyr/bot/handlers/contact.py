from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from moydodyr.bot.services.screen import clear_flow, show_screen
from moydodyr.bot.states import Form

router = Router()
router.message.filter(F.chat.type == "private")


@router.message(Form.manager_message)
@router.message(F.text == "📞 Связаться с менеджером")
async def contact_manager(message: Message, state: FSMContext):
    await clear_flow(state)
    await show_screen(
        message,
        state,
        "Свяжитесь с администратором: @Moydodyr_msk.\n\n"
        "Нажмите «Написать администратору», чтобы открыть личный чат в Telegram.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✉️ Написать администратору", url="https://t.me/Moydodyr_msk"
                    )
                ]
            ],
        ),
    )
