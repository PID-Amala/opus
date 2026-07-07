"""
Full Telegram bot loop. Run this alongside your API:

    python run_telegram_bot.py

- First message from a new chat: expects a phone number, links it.
- Every message after that: forwarded to your own /chat endpoint (same
  GeminiLLM sessions as Swagger testing), reply sent back via Telegram.

Long-polls Telegram - no public URL or ngrok needed.
"""

import asyncio
import re

import httpx

from opus_escrow.db.client import close_client
from opus_escrow.integrations.telegram import get_updates, send_telegram_message
from opus_escrow.repositories.users import get_user_by_telegram_chat_id, link_telegram_chat_id
from opus_escrow.config import get_settings

settings = get_settings()


PHONE_PATTERN = re.compile(r"^\+?\d{10,15}$")
CHAT_ENDPOINT = settings.chat_endpoint_url  # your local FastAPI server


async def forward_to_chat(session_id: str, message: str) -> str:
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(CHAT_ENDPOINT, json={"session_id": session_id, "message": message})
    response.raise_for_status()
    return response.json()["reply"]


async def main() -> None:
    print("Telegram bot running - message it to link a number, then chat normally. Ctrl+C to stop.")
    offset = 0

    while True:
        updates = await get_updates(offset=offset)
        for update in updates:
            offset = update["update_id"] + 1
            message = update.get("message")
            if not message or "text" not in message:
                continue

            chat_id = str(message["chat"]["id"])
            text = message["text"].strip()

            if text == "/start":
                await send_telegram_message(chat_id, "Send me your registered phone number to link this chat.")
                continue

            linked_user = await get_user_by_telegram_chat_id(chat_id)

            if not linked_user:
                if PHONE_PATTERN.match(text):
                    cleaned = text.lstrip("+")
                    await link_telegram_chat_id(cleaned, chat_id)
                    await send_telegram_message(chat_id, f"Linked! You can now chat normally as {cleaned}.")
                    print(f"Linked {cleaned} -> chat_id {chat_id}")
                else:
                    await send_telegram_message(chat_id, "Send me your phone number first to link this chat.")
                continue

            # Already linked - forward everything to the AI, including
            # numeric messages like a BVN, which would otherwise look
            # like a phone number to the regex above.
            try:
                reply = await forward_to_chat(linked_user["whatsapp_number"], text)
                await send_telegram_message(chat_id, reply)
            except Exception as exc:
                print(f"[chat forward error] {exc}")
                await send_telegram_message(chat_id, "Something went wrong processing that - try again.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    finally:
        asyncio.run(close_client())