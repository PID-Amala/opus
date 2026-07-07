from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Query
from pydantic import BaseModel
import re

from opus_escrow.integrations.telegram import send_telegram_message
from opus_escrow.repositories.users import (
    get_user_by_telegram_chat_id,
    link_telegram_chat_id,
)

from opus_escrow.db.client import close_client, get_client, get_database
from opus_escrow.integrations.gemini_llm import GeminiLLM
from opus_escrow.integrations.whatsapp import send_whatsapp_message
from opus_escrow.config import get_settings

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = get_client()
    await client.admin.command("ping")
    yield
    await close_client()


app = FastAPI(title="Opus Escrow", lifespan=lifespan)

@app.get("/")
async def root():
    return {"status": "ok", "service": "Opus Escrow API"}


# Shared Gemini sessions
# WhatsApp -> keyed by phone number
# Telegram -> keyed by linked WhatsApp number
# Chat API -> keyed by session_id
sessions: dict[str, GeminiLLM] = {}


async def chat_with_ai(session_id: str, message: str) -> str:
    if session_id not in sessions:
        sessions[session_id] = GeminiLLM()

    ai_session = sessions[session_id]
    return await ai_session.send(message)


# ==============================================================================
# DEMO CHAT ENDPOINT
# ==============================================================================

class ChatRequest(BaseModel):
    session_id: str
    message: str


class ChatResponse(BaseModel):
    reply: str


@app.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest):
    reply = await chat_with_ai(
        payload.session_id,
        payload.message,
    )

    return ChatResponse(reply=reply)


@app.delete("/chat/{session_id}")
async def reset_chat_session(session_id: str):
    sessions.pop(session_id, None)

    return {
        "status": "cleared",
        "session_id": session_id,
    }


# ==============================================================================
# WHATSAPP WEBHOOK
# ==============================================================================

@app.get("/webhook")
async def verify_webhook(
    mode: str = Query(None, alias="hub.mode"),
    token: str = Query(None, alias="hub.verify_token"),
    challenge: str = Query(None, alias="hub.challenge"),
):
    if mode == "subscribe" and token == settings.whatsapp_verify_token:
        print("--- WEBHOOK CONNECTED TO META ---")
        return int(challenge)

    return "Verification failed", 403


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()

    try:
        value = data["entry"][0]["changes"][0]["value"]

        if "messages" not in value:
            return {"status": "ok"}

        message = value["messages"][0]

        sender_number = message["from"]
        user_text = message["text"]["body"]

        final_ai_text = await chat_with_ai(
            sender_number,
            user_text,
        )

        await send_whatsapp_message(
            to_number=sender_number,
            message=final_ai_text,
        )

        return {"status": "success"}

    except Exception as e:
        print(f"Webhook Error: {e}")
        return {"status": "error"}


# ==============================================================================
# HEALTH CHECK
# ==============================================================================

@app.get("/health")
async def health():
    db = get_database()
    collections = await db.list_collection_names()

    return {
        "status": "ok",
        "database": db.name,
        "collections": collections,
    }


# ==============================================================================
# TELEGRAM WEBHOOK
# ==============================================================================

PHONE_PATTERN = re.compile(r"^\+?\d{10,15}$")


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    update = await request.json()

    message = update.get("message")

    if not message or "text" not in message:
        return {"ok": True}

    chat_id = str(message["chat"]["id"])
    text = message["text"].strip()


    if text == "/start":
        await send_telegram_message(
            chat_id,
            "Send me your registered phone number to link this chat.",
        )

        return {"ok": True}


    linked_user = await get_user_by_telegram_chat_id(chat_id)


    # First time user
    if not linked_user:

        if PHONE_PATTERN.match(text):
            cleaned = text.lstrip("+")

            await link_telegram_chat_id(
                cleaned,
                chat_id,
            )

            await send_telegram_message(
                chat_id,
                f"Linked! You can now chat normally as {cleaned}.",
            )

            print(
                f"Linked {cleaned} -> chat_id {chat_id}"
            )

        else:
            await send_telegram_message(
                chat_id,
                "Send me your phone number first to link this chat.",
            )

        return {"ok": True}


    # Existing user
    try:

        reply = await chat_with_ai(
            linked_user["whatsapp_number"],
            text,
        )

        await send_telegram_message(
            chat_id,
            reply,
        )


    except Exception as exc:

        print(
            f"[telegram chat error] {exc}"
        )

        await send_telegram_message(
            chat_id,
            "Something went wrong processing that - try again.",
        )


    return {"ok": True}