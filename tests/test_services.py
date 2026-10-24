from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.methods import EditMessageMedia, SendMessage, SendPhoto

from moydodyr.bot.formatting import services_text
from moydodyr.bot.handlers.menu import services
from moydodyr.bot.services.screen import SCREEN_IMAGES, SCREEN_KEY
from tests.test_welcome import load_catalog


def test_services_prices_use_catalog_and_hide_disabled_items():
    catalog = load_catalog()
    catalog["services"]["regular"]["rate_flat"] = 123.5
    catalog["services"]["regular"]["rate_house"] = 0
    catalog["services"]["regular"]["minimum"] = 5678
    catalog["services"]["general"]["enabled"] = False
    catalog["extras"]["windows"]["price"] = 765
    catalog["extras"]["sofa"]["enabled"] = False
    text = services_text(catalog)
    assert "Квартира: от 123,5 ₽/м²" in text
    assert "Дом / коттедж: от 0 ₽/м²" in text
    assert "Минимальная стоимость: 5 678 ₽" in text
    assert "Мойка окон (за створку): от 765 ₽" in text
    assert "Генеральная уборка" not in text
    assert "Химчистка дивана" not in text


def test_services_missing_prices_are_not_shown_as_free():
    catalog = load_catalog()
    catalog["services"]["regular"].update(rate_flat=None, rate_house=None, minimum=None)
    catalog["extras"]["windows"]["price"] = None
    text = services_text(catalog)
    assert "Квартира: цену уточнит менеджер" in text
    assert "Минимальную стоимость уточнит менеджер" in text
    assert "Мойка окон (за створку): цену уточнит менеджер" in text
    assert "от 0 ₽" not in text


async def test_services_replaces_image_in_current_message(screen_env):
    _, session, state, message = screen_env
    await state.update_data(**{SCREEN_KEY: {"message_id": 100, "photo": True}})
    await services(message, SimpleNamespace(catalog=AsyncMock(return_value=load_catalog())), state)
    edit = next(call for call in session.calls if isinstance(call, EditMessageMedia))
    assert edit.message_id == 100
    assert edit.media.media.path == SCREEN_IMAGES["services"]
    assert SCREEN_IMAGES["services"].is_file()
    assert "Услуги и цены" in edit.media.caption
    assert "от 90 ₽/м²" in edit.media.caption
    assert not any(isinstance(call, (SendMessage, SendPhoto)) for call in session.calls)
