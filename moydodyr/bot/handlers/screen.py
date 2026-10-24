import inspect

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from moydodyr.bot.handlers import menu, orders
from moydodyr.bot.services.photos import flush_user_albums
from moydodyr.bot.services.screen import SCREEN_KEY, bind_screen, go_back, render_page, show_screen
from moydodyr.bot.states import Form
from moydodyr.config import Settings
from moydodyr.db import Database

router = Router()
router.callback_query.filter(F.message.chat.type == "private")

HANDLERS = {
    Form.object.state: orders.choose_object,
    Form.service.state: orders.choose_service,
    Form.extras.state: orders.choose_extras,
    Form.estimate_offer.state: orders.continue_from_estimate,
    Form.comment.state: orders.choose_comment,
    Form.photos.state: orders.finish_photos,
    Form.confirm.state: orders.confirm,
}


@router.callback_query(F.data.startswith("screen:"))
async def screen_action(query: CallbackQuery, state: FSMContext, db: Database, settings: Settings):
    if not isinstance(query.message, Message) or not await bind_screen(query.message, state):
        await query.answer("Это старый экран. Откройте /start.")
        return
    screen = (await state.get_data())[SCREEN_KEY]
    parts = (query.data or "").split(":")
    if len(parts) != 4 or parts[2] != screen.get("nonce") or not parts[3].isdigit():
        await query.answer("Этот шаг уже завершён. Используйте текущие кнопки.")
        return
    index = int(parts[3])
    if parts[1] == "back":
        await query.answer()
        if await state.get_state() == Form.photos.state:
            await flush_user_albums(
                query.message.model_copy(update={"from_user": query.from_user}), state
            )
        await go_back(query.message, state)
        return
    if parts[1] == "page" and index < len(screen["pages"]):
        await query.answer()
        await render_page(query.message, screen, index)
        return
    if parts[1] != "pick" or index >= len(screen.get("buttons", [])):
        await query.answer()
        return
    text = screen["buttons"][index]
    current_state = await state.get_state()
    if text == "↩️ Отмена":
        handler = menu.cancel
    elif text == "__contact_help__":
        await query.answer()
        prompt = (
            "Введите телефон в формате +7 999 123-45-67 или отправьте контакт через "
            "скрепку → «Контакт»."
            if current_state == Form.phone.state
            else "Отправьте свой контакт через скрепку → «Контакт», "
            "затем напишите сообщение менеджеру."
        )
        await show_screen(query.message, state, prompt)
        return
    else:
        handler = HANDLERS.get(current_state)
    if handler is None:
        await query.answer("Этот шаг уже завершён. Вернитесь в главное меню.")
        return
    await query.answer()
    # Treat an inline choice as input from the clicking user, never from the bot.
    message = query.message.model_copy(
        update={
            "from_user": query.from_user,
            "text": text,
            "photo": None,
            "caption": None,
        }
    )
    arguments = {"message": message, "state": state, "db": db, "settings": settings}
    await handler(
        **{
            key: value
            for key, value in arguments.items()
            if key in inspect.signature(handler).parameters
        }
    )
