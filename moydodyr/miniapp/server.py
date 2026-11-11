import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from aiogram.utils.web_app import safe_parse_webapp_init_data
from aiohttp import web
from dotenv import load_dotenv

from moydodyr.bot.services.photo_archive import MAX_PHOTO_SIZE, image_type
from moydodyr.config import Settings
from moydodyr.db import Database
from moydodyr.domain import area_value, date_value, estimate, phone_value, time_value

STATIC = Path(__file__).parent / "static"
DB_KEY = web.AppKey("db", Database)
SETTINGS_KEY = web.AppKey("settings", Settings)


def validate_user(token: str, value: str):
    try:
        data = safe_parse_webapp_init_data(token, value)
        age = (datetime.now(timezone.utc) - data.auth_date).total_seconds()
        if not data.user or not -60 <= age <= 86400:
            raise ValueError
        return data.user
    except (ValueError, TypeError):
        raise web.HTTPUnauthorized(text="Откройте приложение из Telegram заново.") from None


def selection(data: dict, catalog: dict):
    obj, service = data.get("object"), data.get("service")
    extras = data.get("extras", [])
    if not isinstance(obj, str) or not isinstance(service, str):
        raise ValueError("Выберите объект и вид уборки.")
    if obj not in catalog["objects"] or service not in catalog["services"]:
        raise ValueError("Выберите объект и вид уборки.")
    if not catalog["services"][service].get("enabled"):
        raise ValueError("Выбранная услуга больше недоступна.")
    if (
        not isinstance(extras, list)
        or any(not isinstance(x, str) for x in extras)
        or len(extras) != len(set(extras))
    ):
        raise ValueError("Проверьте дополнительные услуги.")
    if any(
        key not in catalog["extras"] or not catalog["extras"][key].get("enabled") for key in extras
    ):
        raise ValueError("Выбранная дополнительная услуга недоступна.")
    return {
        "object": obj,
        "service": service,
        "area": area_value(str(data.get("area", ""))),
        "extras": extras,
    }


def order_data(data: dict, catalog: dict, user_id: int):
    draft = selection(data, catalog)
    try:
        draft_id = str(UUID(data["draft_id"]))
        selected_date = date.fromisoformat(data["date"])
        day = date_value(selected_date.strftime("%d.%m.%Y"))
    except (ValueError, KeyError, TypeError):
        raise ValueError("Выберите корректную дату.") from None
    address = str(data.get("address", "")).strip()
    name = str(data.get("name", "")).strip()
    comment = str(data.get("comment", "")).strip()
    photos = data.get("photos", [])
    if not 5 <= len(address) <= 500:
        raise ValueError("Адрес должен содержать от 5 до 500 символов.")
    if not 2 <= len(name) <= 100:
        raise ValueError("Имя должно содержать от 2 до 100 символов.")
    if len(comment) > 1500:
        raise ValueError("Комментарий должен быть не длиннее 1500 символов.")
    if (
        not isinstance(photos, list)
        or len(photos) > 5
        or any(not isinstance(p, str) or not p.startswith("miniapp:") for p in photos)
        or len(set(photos)) != len(photos)
    ):
        raise ValueError("Проверьте список фотографий.")
    draft.update(
        draft_id=draft_id,
        date=day,
        time=time_value(str(data.get("time") or "любое")),
        address=address,
        name=name,
        phone=phone_value(str(data.get("phone", ""))),
        comment=comment,
        photos=photos,
        telegram_user=user_id,
        mode="order",
    )
    draft["estimate"] = estimate(catalog, draft)
    return draft


@web.middleware
async def errors(request, handler):
    try:
        return await handler(request)
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=400)


def user(request):
    return validate_user(
        request.app[SETTINGS_KEY].token, request.headers.get("X-Telegram-Init-Data", "")
    )


async def index(request):
    return web.FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


async def catalog_handler(request):
    user(request)
    catalog = await request.app[DB_KEY].catalog()
    return web.json_response(
        {
            "objects": catalog["objects"],
            "services": {
                k: {"title": v["title"]} for k, v in catalog["services"].items() if v.get("enabled")
            },
            "extras": {
                k: {"title": v["title"]} for k, v in catalog["extras"].items() if v.get("enabled")
            },
        }
    )


async def estimate_handler(request):
    user(request)
    data = await request.json()
    catalog = await request.app[DB_KEY].catalog()
    return web.json_response({"estimate": estimate(catalog, selection(data, catalog))})


async def photo_handler(request):
    owner = user(request)
    try:
        draft_id = str(UUID(request.headers.get("X-Draft-Id", "")))
    except ValueError:
        raise web.HTTPBadRequest(text="Некорректный черновик.") from None
    content = await request.read()
    if not 0 < len(content) <= MAX_PHOTO_SIZE:
        raise ValueError("Размер фотографии должен быть не более 20 МБ.")
    try:
        content_type = image_type(content)
    except ValueError:
        raise ValueError("Поддерживаются фотографии JPG, PNG и WebP.") from None
    file_id = "miniapp:" + str(uuid4())
    await request.app[DB_KEY].archive_photo(owner.id, draft_id, file_id, content, content_type)
    return web.json_response({"file_id": file_id})


async def submit_handler(request):
    owner = user(request)
    data = await request.json()
    db, settings = request.app[DB_KEY], request.app[SETTINGS_KEY]
    catalog = await db.catalog()
    draft = order_data(data, catalog, owner.id)
    if not await db.draft_photos_exist(owner.id, draft["draft_id"], draft["photos"]):
        raise ValueError("Не удалось найти фотографии. Загрузите их снова.")
    webhook = None
    if settings.webhook_enabled:
        webhook = {
            "name": draft["name"],
            "phone": draft["phone"],
            "telegram": f"@{owner.username}" if owner.username else str(owner.id),
            "service": catalog["services"][draft["service"]]["title"],
            "address": draft["address"],
            "date": draft["date"],
            "price": (draft["estimate"] or {}).get("total"),
            "comment": draft["comment"],
            "object": catalog["objects"][draft["object"]],
            "area": draft["area"],
            "time": draft["time"],
            "extras": [catalog["extras"][key]["title"] for key in draft["extras"]],
        }
    order = await db.submit(
        owner.id, draft, settings.manager_chat_id, catalog["accepted"], webhook_payload=webhook
    )
    return web.json_response({"order_id": order["id"]})


def create_app(db, settings):
    app = web.Application(middlewares=[errors], client_max_size=MAX_PHOTO_SIZE + 1024)
    app[DB_KEY], app[SETTINGS_KEY] = db, settings
    app.router.add_get("/", index)
    app.router.add_get("/api/catalog", catalog_handler)
    app.router.add_post("/api/estimate", estimate_handler)
    app.router.add_post("/api/photo", photo_handler)
    app.router.add_post("/api/submit", submit_handler)
    app.router.add_static("/static/", STATIC)
    return app


async def start_app():
    settings = Settings.load()
    db = await Database.connect(settings.database_url)
    app = create_app(db, settings)

    async def close_db(app):
        await db.pool.close()

    app.on_cleanup.append(close_db)
    return app


def main():
    logging.basicConfig(level=logging.INFO)
    load_dotenv()
    web.run_app(
        start_app(),
        host=os.getenv("MINI_APP_HOST", "127.0.0.1"),
        port=int(os.getenv("MINI_APP_PORT", "8080")),
    )


if __name__ == "__main__":
    main()
