"""
Telegram bot integration - sends messages to users who've linked their
phone number to a Telegram chat via the linking script.

Unlike WhatsApp/Nomba, Telegram bots support long-polling (getUpdates),
so this works today with zero public URL or business verification.
"""

import httpx

from opus_escrow.config import get_settings


class TelegramAPIError(Exception):
    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"Telegram API error {status_code}: {body}")


async def send_telegram_message(chat_id: str, message: str) -> dict:
    """Sends a message to a raw Telegram chat_id."""
    settings = get_settings()
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"

    async with httpx.AsyncClient() as client:
        response = await client.post(url, json={"chat_id": chat_id, "text": message})

    if response.status_code != 200:
        raise TelegramAPIError(response.status_code, response.text)
    return response.json()


async def send_telegram_message_by_phone(to_number: str, message: str) -> dict:
    """
    Resolves a phone number to their linked Telegram chat_id, then sends.
    This is what Gemini calls - it still thinks in terms of phone
    numbers (same as the rest of the app), the Telegram lookup is
    transparent to it.
    """
    from opus_escrow.repositories.users import get_user_by_whatsapp

    user = await get_user_by_whatsapp(to_number)
    if not user or not user.get("telegram_chat_id"):
        raise ValueError(
            f"No linked Telegram chat for {to_number} - they need to message "
            f"the bot and link their number first (see run_telegram_linker.py)."
        )
    return await send_telegram_message(user["telegram_chat_id"], message)


async def get_updates(offset: int = 0) -> list[dict]:
    """Long-polls Telegram for new incoming messages. Used by the linker script."""
    settings = get_settings()
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/getUpdates"

    async with httpx.AsyncClient(timeout=35.0) as client:
        response = await client.get(url, params={"offset": offset, "timeout": 30})

    if response.status_code != 200:
        raise TelegramAPIError(response.status_code, response.text)
    return response.json().get("result", [])