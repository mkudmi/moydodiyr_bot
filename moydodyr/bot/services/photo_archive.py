import logging
from io import BytesIO

from aiogram import Bot

from moydodyr.db import Database

MAX_PHOTO_SIZE = 20 * 1024 * 1024


class PhotoBuffer(BytesIO):
    def write(self, data):
        if self.tell() + len(data) > MAX_PHOTO_SIZE:
            raise ValueError("Photo exceeds archive size limit")
        return super().write(data)


def image_type(content: bytes) -> str:
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("Unsupported photo format")


async def archive_telegram_photo(
    bot: Bot, db: Database, user_id: int, draft_id: str, file_id: str, photo=None
):
    existing = await db.draft_photo(user_id, draft_id, file_id)
    if existing:
        return existing
    file = await bot.get_file(file_id)
    if file.file_size and file.file_size > MAX_PHOTO_SIZE:
        raise ValueError("Photo exceeds archive size limit")
    buffer = PhotoBuffer()
    await bot.download_file(file.file_path, destination=buffer)
    content = buffer.getvalue()
    if file.file_size is not None and len(content) != file.file_size:
        raise ValueError("Incomplete photo download")
    return await db.archive_photo(
        user_id,
        draft_id,
        file_id,
        content,
        image_type(content),
        unique_id=file.file_unique_id,
        width=photo.width if photo else None,
        height=photo.height if photo else None,
    )


async def ensure_draft_photos(bot: Bot, db: Database, user_id: int, draft: dict):
    for file_id in draft.get("photos", []):
        await archive_telegram_photo(bot, db, user_id, draft["draft_id"], file_id)


async def backfill_order_photos(bot: Bot, db: Database):
    archived, failed = 0, 0
    after_order, after_position = 0, -1
    while True:
        try:
            pending = await db.unarchived_order_photos(
                after_order_id=after_order,
                after_position=after_position,
            )
        except Exception as error:
            logging.warning("Photo backfill unavailable (%s)", type(error).__name__)
            break
        if not pending:
            break
        for item in pending:
            try:
                photo = await archive_telegram_photo(
                    bot,
                    db,
                    item["user_id"],
                    str(item["draft_id"]),
                    item["telegram_file_id"],
                )
                result = await db.link_order_photo(
                    item["user_id"],
                    item["order_id"],
                    item["position"],
                    photo["id"],
                )
                if result != "UPDATE 1":
                    raise ValueError("Photo could not be linked to its order")
                archived += 1
            except Exception as error:
                failed += 1
                logging.warning("Existing photo archival failed (%s)", type(error).__name__)
        after_order, after_position = pending[-1]["order_id"], pending[-1]["position"]
    logging.info("Photo backfill: archived=%s failed=%s", archived, failed)
    return archived, failed
