from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from moydodyr.config import Settings

group_router = Router()
group_router.message.filter(F.chat.type.in_({"group", "supergroup"}))


async def show_group_admin_panel(message: Message, settings: Settings):
    if (
        message.chat.id != settings.manager_chat_id
        or message.from_user.id not in settings.admin_ids
    ):
        await message.answer("Панель доступна администраторам в рабочей группе.")
        return
    await message.answer(
        "Управление заявками перенесено в CRM. "
        "Здесь остаются уведомления о новых обращениях. "
        "Настройки каталога: /admin в личном чате с ботом.",
        reply_markup=ReplyKeyboardRemove(),
    )


@group_router.message(CommandStart())
async def group_start(message: Message, settings: Settings):
    await show_group_admin_panel(message, settings)


@group_router.message(Command("admin"))
@group_router.message(
    F.text.in_({"📬 Новые заявки", "🛠 Заявки в работе", "⚙️ Настройки", "ℹ️ Команды администратора"})
)
async def group_admin(message: Message, settings: Settings):
    await show_group_admin_panel(message, settings)


@group_router.callback_query(F.data.startswith("order:"))
async def order_action(query: CallbackQuery, settings: Settings):
    if query.from_user.id not in settings.admin_ids:
        await query.answer("Недостаточно прав.", show_alert=True)
        return
    await query.answer(
        "Управление заявками перенесено в CRM. Откройте карточку на сайте.", show_alert=True
    )
