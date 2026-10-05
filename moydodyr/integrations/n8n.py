import asyncio
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class PermanentWebhookError(Exception):
    pass


def post_n8n_lead(url: str, secret: str, lead: dict) -> str | int | None:
    body = {**lead, "secret": secret}
    request = Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            status = response.status
            content = response.read()
    except HTTPError as error:
        if error.code == 401:
            raise PermanentWebhookError("n8n returned HTTP 401; check N8N_WEBHOOK_SECRET") from None
        raise RuntimeError(f"n8n returned HTTP {error.code}") from None
    except URLError as error:
        raise RuntimeError(f"n8n connection failed: {type(error.reason).__name__}") from None
    if status != 200:
        raise RuntimeError(f"n8n returned HTTP {status}")
    try:
        result = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise RuntimeError("n8n returned invalid JSON") from None
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError("n8n response did not confirm success")
    return result.get("lead_id")


async def send_n8n_lead(url: str, secret: str, lead: dict) -> str | int | None:
    return await asyncio.to_thread(post_n8n_lead, url, secret, lead)
