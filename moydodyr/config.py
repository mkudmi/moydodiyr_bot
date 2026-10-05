import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    token: str
    database_url: str
    manager_chat_id: int
    admin_ids: frozenset[int]

    @classmethod
    def load(cls):
        load_dotenv()
        required = ["BOT_TOKEN", "DATABASE_URL", "MANAGER_CHAT_ID", "ADMIN_IDS"]
        missing = [name for name in required if not os.getenv(name, "").strip()]
        if missing:
            raise ValueError("Заполните .env: " + ", ".join(missing))
        return cls(
            os.environ["BOT_TOKEN"].strip(),
            os.environ["DATABASE_URL"].strip(),
            int(os.environ["MANAGER_CHAT_ID"]),
            frozenset(int(x.strip()) for x in os.environ["ADMIN_IDS"].split(",")),
        )
