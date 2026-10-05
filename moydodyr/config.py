import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    token: str
    database_url: str
    manager_chat_id: int
    admin_ids: frozenset[int]
    n8n_webhook_url: str | None
    n8n_webhook_secret: str | None

    @classmethod
    def load(cls):
        load_dotenv()
        required = ["BOT_TOKEN", "DATABASE_URL", "MANAGER_CHAT_ID", "ADMIN_IDS"]
        missing = [name for name in required if not os.getenv(name, "").strip()]
        if missing:
            raise ValueError("Заполните .env: " + ", ".join(missing))
        webhook_url = os.getenv("N8N_WEBHOOK_URL", "").strip() or None
        webhook_secret = os.getenv("N8N_WEBHOOK_SECRET", "").strip() or None
        if bool(webhook_url) != bool(webhook_secret):
            raise ValueError("Заполните обе переменные N8N_WEBHOOK_URL и N8N_WEBHOOK_SECRET.")
        if webhook_url and not webhook_url.startswith("https://"):
            raise ValueError("N8N_WEBHOOK_URL должен использовать HTTPS.")
        return cls(
            token=os.environ["BOT_TOKEN"].strip(),
            database_url=os.environ["DATABASE_URL"].strip(),
            manager_chat_id=int(os.environ["MANAGER_CHAT_ID"]),
            admin_ids=frozenset(int(x.strip()) for x in os.environ["ADMIN_IDS"].split(",")),
            n8n_webhook_url=webhook_url,
            n8n_webhook_secret=webhook_secret,
        )

    @property
    def webhook_enabled(self) -> bool:
        return bool(self.n8n_webhook_url and self.n8n_webhook_secret)
