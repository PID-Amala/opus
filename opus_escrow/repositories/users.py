"""
Users repository - onboarding, profile updates, and verification.

Plain async functions, no FastAPI dependency, so these are callable from
HTTP routes today and from Gemini function-calling handlers later without
duplicating logic.
"""

from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId

from opus_escrow.db.client import get_database
from opus_escrow.integrations.verification import call_verification_service


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def get_user_by_whatsapp(whatsapp_number: str) -> Optional[dict]:
    db = get_database()
    return await db.users.find_one({"whatsapp_number": whatsapp_number})


async def get_or_create_user(whatsapp_number: str) -> dict:
    """
    Call this on the first inbound message from a WhatsApp number.
    Returns the existing user doc if one exists, otherwise creates a
    fresh unverified one and logs the creation event.
    """
    db = get_database()
    existing = await get_user_by_whatsapp(whatsapp_number)
    if existing:
        return existing

    now = _now()
    new_user = {
        "whatsapp_number": whatsapp_number,
        "verification_status": "unverified",
        "created_at": now,
        "updated_at": now,
    }
    result = await db.users.insert_one(new_user)
    new_user["_id"] = result.inserted_id

    await log_user_event(result.inserted_id, "user_created", {"whatsapp_number": whatsapp_number})
    return new_user


async def update_profile(user_id: ObjectId, **fields: Any) -> None:
    """
    Update basic onboarding fields (first_name, last_name, location).
    Never pass a raw verification number through here - that only ever
    goes through request_verification() below, which doesn't persist it.
    """
    db = get_database()
    fields["updated_at"] = _now()
    await db.users.update_one({"_id": user_id}, {"$set": fields})
    await log_user_event(user_id, "profile_updated", {"fields": list(fields.keys())})


async def request_verification(user_id: ObjectId, method: str, verification_number: str) -> dict:
    """
    Kicks off verification for a user.

    The raw verification_number is used only in this function - passed
    straight to call_verification_service() and never written to Mongo
    or to any log. Only the returned opaque reference token is stored.

    Returns the updated user document.
    """
    db = get_database()

    await db.users.update_one(
        {"_id": user_id},
        {"$set": {"verification_status": "pending", "verification_method": method, "updated_at": _now()}},
    )
    await log_user_event(user_id, "verification_attempted", {"method": method})

    result = await call_verification_service(method, verification_number)

    new_status = "verified" if result["success"] else "failed"
    await db.users.update_one(
        {"_id": user_id},
        {
            "$set": {
                "verification_status": new_status,
                "verification_ref": result["reference"],
                "updated_at": _now(),
            }
        },
    )
    await log_user_event(
        user_id,
        "verification_completed" if result["success"] else "verification_failed",
        {"method": method, "reference": result["reference"]},
    )

    return await db.users.find_one({"_id": user_id})


async def log_user_event(user_id: ObjectId, event_type: str, payload: Optional[dict] = None) -> None:
    db = get_database()
    await db.user_logs.insert_one(
        {
            "user_id": user_id,
            "event_type": event_type,
            "payload": payload or {},
            "created_at": _now(),
        }
    )
async def link_telegram_chat_id(whatsapp_number: str, chat_id: str) -> dict:
    """
    Links a user's phone number to their Telegram chat_id, so notifications
    can be sent via send_telegram_message_by_phone(). Creates the user if
    they don't exist yet (someone might link Telegram before ever going
    through onboarding).
    """
    user = await get_or_create_user(whatsapp_number)
    db = get_database()
    await db.users.update_one(
        {"_id": user["_id"]}, {"$set": {"telegram_chat_id": chat_id, "updated_at": _now()}}
    )
    await log_user_event(user["_id"], "telegram_linked", {"chat_id": chat_id})
    return await db.users.find_one({"_id": user["_id"]})


async def get_user_by_telegram_chat_id(chat_id: str) -> Optional[dict]:
    db = get_database()
    return await db.users.find_one({"telegram_chat_id": chat_id})

async def get_user_by_id(user_id: ObjectId) -> Optional[dict]:
    db = get_database()
    return await db.users.find_one({"_id": user_id})