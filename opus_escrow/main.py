from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Query
from pydantic import BaseModel

from opus_escrow.db.client import close_client, get_client, get_database
from opus_escrow.integrations.gemini_llm import GeminiLLM
from opus_escrow.integrations.whatsapp import send_whatsapp_message
from opus_escrow.config import get_settings

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = get_client()
    await client.admin.command("ping")  # fail fast if MONGO_URI is bad
    yield
    await close_client()


app = FastAPI(title="Opus Escrow", lifespan=lifespan)

# Session storage (identifier -> Gemini Instance).
# Shared between the WhatsApp webhook (keyed by phone number) and the
# demo chat endpoint below (keyed by whatever session_id the frontend
# sends) - same AI/repository layer either way, only the transport differs.
sessions: dict[str, GeminiLLM] = {}


# ==============================================================================
# DEMO CHAT ENDPOINT - stands in for WhatsApp while Meta verification is
# pending. Same GeminiLLM sessions, same function-calling, same repository
# calls - just a plain HTTP request/response instead of a webhook payload.
# ==============================================================================
class ChatRequest(BaseModel):
    session_id: str  # use any test identifier, e.g. "buyer-test", "seller-test"
    message: str


class ChatResponse(BaseModel):
    reply: str


@app.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest):
    if payload.session_id not in sessions:
        sessions[payload.session_id] = GeminiLLM()

    ai_session = sessions[payload.session_id]
    reply = await ai_session.send(payload.message)
    return ChatResponse(reply=reply)


@app.delete("/chat/{session_id}")
async def reset_chat_session(session_id: str):
    """Clears a test session so you can restart a conversation from scratch."""
    sessions.pop(session_id, None)
    return {"status": "cleared", "session_id": session_id}


# ==============================================================================
# WHATSAPP WEBHOOK - left in place for when Meta verification clears.
# Not wired into any frontend right now, just dormant.
# ==============================================================================
@app.get("/webhook")
async def verify_webhook(
    mode: str = Query(None, alias="hub.mode"),
    token: str = Query(None, alias="hub.verify_token"),
    challenge: str = Query(None, alias="hub.challenge"),
):
    """
    This is the Handshake.
    Meta sends a 'challenge' number, and we must send it back
    to prove we own this URL.
    """
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

        if sender_number not in sessions:
            sessions[sender_number] = GeminiLLM()

        ai_session = sessions[sender_number]
        final_ai_text = await ai_session.send(user_text)

        await send_whatsapp_message(to_number=sender_number, message=final_ai_text)

        return {"status": "success"}
    except Exception as e:
        print(f"Webhook Error: {e}")
        return {"status": "error"}


@app.get("/health")
async def health():
    db = get_database()
    collections = await db.list_collection_names()
    return {"status": "ok", "database": db.name, "collections": collections}