from copy import deepcopy
from decimal import Decimal

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    Message,
)

from moydodyr.config import Settings
from moydodyr.db import Database

router = Router()
router.message.filter(F.chat.type == "private")


@router.message(Command("admin"))
async def admin_command(message: Message, db: Database, settings: Settings):
    if message.from_user.id not in settings.admin_ids:
        await message.answer("Команда доступна только администратору.")
        return
    parts = (message.text or "").split(maxsplit=4)
    if len(parts) == 1:
        await message.answer(
            "Команды администратора:\n"
            "/admin price <service_key> <flat|house> <руб/м²>\n"
            "/admin minimum <service_key> <руб>\n"
            "/admin extra <extra_key> <цена>\n"
            "/admin enabled <service|extra> <key> <true|false>\n"
            "/admin text <welcome|accepted|promotions|reviews|faq> <новый текст>\n"
            "Например: /admin price regular flat 120"
        )
        return
    try:
        catalog = await db.catalog()
        updated = deepcopy(catalog)
        action = parts[1]
        if action == "price" and len(parts) == 5:
            _, _, key, object_key, raw = parts
            field = {"flat": "rate_flat", "house": "rate_house"}.get(object_key)
            if key not in updated["services"] or field is None:
                raise ValueError("Укажите существующий ключ услуги и объект flat или house.")
            updated["services"][key][field] = float(Decimal(raw.replace(",", ".")))
        elif action == "minimum" and len(parts) == 4:
            _, _, key, raw = parts
            if key not in updated["services"]:
                raise ValueError("Неизвестный ключ услуги.")
            updated["services"][key]["minimum"] = float(Decimal(raw.replace(",", ".")))
        elif action == "extra" and len(parts) == 4:
            _, _, key, raw = parts
            if key not in updated["extras"]:
                raise ValueError("Неизвестный ключ дополнительной услуги.")
            updated["extras"][key]["price"] = float(Decimal(raw.replace(",", ".")))
        elif action == "enabled" and len(parts) == 5:
            _, _, kind, key, raw = parts
            group = {"service": "services", "extra": "extras"}.get(kind)
            if group is None or key not in updated[group] or raw.lower() not in {"true", "false"}:
                raise ValueError("Формат: service|extra, ключ и true|false.")
            updated[group][key]["enabled"] = raw.lower() == "true"
        elif action == "text" and len(parts) == 4:
            _, _, key, value = parts
            if key not in {"welcome", "accepted", "promotions", "reviews", "faq"}:
                raise ValueError("Неизвестный текстовый раздел.")
            updated[key] = value
        else:
            raise ValueError("Неизвестная команда или неверное число аргументов. Откройте /admin.")
        await db.change_catalog(lambda current: (current.clear(), current.update(updated)))
    except (ValueError, ArithmeticError) as error:
        await message.answer(f"Не удалось сохранить: {error}")
        return
    await message.answer("Настройки сохранены.")
