import html
from uuid import uuid4

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from moydodyr.bot.keyboards import (
    MENU,
)
from moydodyr.bot.states import Form
from moydodyr.config import Settings
from moydodyr.db import Database

router = Router()
router.message.filter(F.chat.type == "private")


@router.message(F.text == "📞 Связаться с менеджером")
async def contact_manager(message: Message, state: FSMContext):
    await state.set_state(Form.manager_message)
    await message.answer(
        "Напишите сообщение менеджеру. Я передам его вместе с вашим контактом.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Отправить телефон", request_contact=True)],
                [KeyboardButton(text="↩️ Отмена")],
            ],
            resize_keyboard=True,
        ),
    )


@router.message(Form.manager_message, F.contact)
async def manager_contact_only(message: Message, state: FSMContext):
    await state.update_data(contact=message.contact.phone_number)
    await message.answer("Теперь напишите сообщение менеджеру.")


@router.message(Form.manager_message)
async def manager_message(message: Message, state: FSMContext, db: Database, settings: Settings):
    data = await state.get_data()
    text = (message.text or "").strip()
    if len(text) < 1 or len(text) > 3000:
        await message.answer("Сообщение должно быть не длиннее 3000 символов.")
        return
    user = message.from_user
    source = await db.pool.fetchval("SELECT source FROM users WHERE id=$1", user.id)
    request_key = f"contact:{user.id}:{uuid4()}"
    phone = data.get("contact", "не указан")
    manager_payload = {
        "kind": "manager_message",
        "text": (
            f"Сообщение от {html.escape(user.full_name)} "
            f"(@{html.escape(user.username or 'нет username')}, id {user.id})\n"
            f"Телефон: {html.escape(phone)}\n\n{html.escape(text)}"
        ),
    }
    webhook_lead = None
    if settings.webhook_enabled:
        webhook_lead = {
            "name": user.full_name,
            "phone": phone if phone != "не указан" else "",
            "telegram": f"@{user.username}" if user.username else str(user.id),
            "service": "Связаться с менеджером",
            "address": "",
            "date": None,
            "price": None,
            "comment": text,
            "source": source,
            "lead_type": "manager_contact",
        }
    await db.enqueue_contact(
        request_key,
        settings.manager_chat_id,
        manager_payload,
        webhook_lead,
    )
    await state.clear()
    await message.answer(
        "Передал сообщение менеджеру. Он ответит вам напрямую в Telegram.", reply_markup=MENU
    )
