"""
Collection definitions for Opus Escrow.

Each entry defines:
- validator: Mongo server-side JSON Schema validation (enforced on every write)
- indexes: list of {"keys": [...], "options": {...}} passed to create_index

This file is the single source of truth. Run `python -m app.db.init_db`
against any MONGO_URI to bring that database up to the current schema -
this is our equivalent of `manage.py migrate`.
"""

COLLECTIONS = {
    "users": {
        "validator": {
            "$jsonSchema": {
                "bsonType": "object",
                "required": ["whatsapp_number", "verification_status", "created_at", "updated_at"],
                "properties": {
                    "whatsapp_number": {"bsonType": "string"},
                    "first_name": {"bsonType": "string"},
                    "last_name": {"bsonType": "string"},
                    "location": {"bsonType": "string"},
                    "verification_status": {
                        "enum": ["unverified", "pending", "verified", "failed"]
                    },
                    "verification_method": {"bsonType": "string"},
                    "verification_ref": {"bsonType": "string"},
                    "created_at": {"bsonType": "date"},
                    "updated_at": {"bsonType": "date"},
                    "telegram_chat_id": {"bsonType": "string"},
                },
            }
        },
        "indexes": [
            {"keys": [("whatsapp_number", 1)], "options": {"unique": True}},
        ],
    },
    "user_logs": {
        "validator": {
            "$jsonSchema": {
                "bsonType": "object",
                "required": ["user_id", "event_type", "created_at"],
                "properties": {
                    "user_id": {"bsonType": "objectId"},
                    "event_type": {"bsonType": "string"},
                    "payload": {"bsonType": "object"},
                    "created_at": {"bsonType": "date"},
                },
            }
        },
        "indexes": [
            {"keys": [("user_id", 1), ("created_at", -1)]},
        ],
    },
    "transactions": {
        "validator": {
            "$jsonSchema": {
                "bsonType": "object",
                "required": ["transaction_ref", "status", "created_at", "updated_at"],
                "properties": {
                    "transaction_ref": {"bsonType": "string"},
                    "status": {
                    "enum": [
                        "pending_acceptance",
                        "no_acceptance",
                        "cancelled",
                        "awaiting_payment",
                        "expired",
                        "funds_held",
                        "disputed",
                        "payout_processing",
                        "completed",
                    ]

                    },
                    "initiator_id": {"bsonType": "objectId"},
                    "counterparty_id": {"bsonType": "objectId"},
                    "buyer_id": {"bsonType": "objectId"},
                    "seller_id": {"bsonType": "objectId"},
                    "item_description": {"bsonType": "string"},
                    "amount": {"bsonType": "double"},
                    "currency": {"bsonType": "string"},
                    "opus_fee": {"bsonType": "double"},
                    "payment": {"bsonType": "object"},
                    "timestamps": {"bsonType": "object"},
                    "dispute_id": {"bsonType": "objectId"},
                    "created_at": {"bsonType": "date"},
                    "updated_at": {"bsonType": "date"},
                },
            }
        },
        "indexes": [
            {"keys": [("transaction_ref", 1)], "options": {"unique": True}},
            {"keys": [("status", 1)]},
            {"keys": [("buyer_id", 1)]},
            {"keys": [("seller_id", 1)]},
        ],
    },
    "transaction_logs": {
        "validator": {
            "$jsonSchema": {
                "bsonType": "object",
                "required": ["transaction_id", "event_type", "created_at"],
                "properties": {
                    "transaction_id": {"bsonType": "objectId"},
                    "from_status": {"bsonType": "string"},
                    "to_status": {"bsonType": "string"},
                    "actor": {"enum": ["buyer", "seller", "system", "admin", "gemini"]},
                    "event_type": {"bsonType": "string"},
                    "metadata": {"bsonType": "object"},
                    "created_at": {"bsonType": "date"},
                },
            }
        },
        "indexes": [
            {"keys": [("transaction_id", 1), ("created_at", -1)]},
        ],
    },
    "messages": {
        "validator": {
            "$jsonSchema": {
                "bsonType": "object",
                "required": ["user_id", "direction", "created_at"],
                "properties": {
                    "user_id": {"bsonType": "objectId"},
                    "direction": {"enum": ["inbound", "outbound"]},
                    "whatsapp_message_id": {"bsonType": "string"},
                    "text": {"bsonType": "string"},
                    "redacted": {"bsonType": "bool"},
                    "transaction_id": {"bsonType": "objectId"},
                    "function_call": {"bsonType": "object"},
                    "created_at": {"bsonType": "date"},
                },
            }
        },
        "indexes": [
            {"keys": [("user_id", 1), ("created_at", -1)]},
            {"keys": [("transaction_id", 1), ("created_at", -1)]},
        ],
    },
    "conversation_state": {
        # _id is the user_id itself - one doc per user, cheap upsert target
        "validator": {
            "$jsonSchema": {
                "bsonType": "object",
                "required": ["current_flow", "updated_at"],
                "properties": {
                    "current_flow": {
                        "enum": [
                            "onboarding",
                            "creating_transaction",
                            "awaiting_payment",
                            "dispute",
                            "idle",
                        ]
                    },
                    "known_slots": {"bsonType": "object"},
                    "active_transaction_id": {"bsonType": "objectId"},
                    "summary": {"bsonType": "string"},
                    "last_evaluated_at": {"bsonType": "date"},
                    "updated_at": {"bsonType": "date"},
                },
            }
        },
        "indexes": [],
    },
}
