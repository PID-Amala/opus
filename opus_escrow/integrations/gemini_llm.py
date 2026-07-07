import asyncio
import json
from typing import Any, Dict, Optional
from bson import ObjectId
from google import genai
from google.genai import types
from opus_escrow.integrations.telegram import send_telegram_message_by_phone
from opus_escrow.config import get_settings
# Repository Imports
from opus_escrow.repositories.users import (
    get_or_create_user, 
    update_profile, 
    request_verification
)
from opus_escrow.repositories.transactions import (
    get_transaction_by_ref,
    create_transaction,
    accept_transaction,
    decline_transaction,
    mark_delivered,
    raise_dispute,
    generate_payment_account,
    initiate_payout,
    confirm_payout,
)

settings = get_settings()

class GeminiKeyRotator:
    """
    Rotates across up to 4 Gemini API keys when one hits its quota limit
    (e.g. free-tier 20 requests/day). Shared module-level state, so
    exhausting a key affects all future calls across every session, not
    just the one that hit the limit.
    """

    def __init__(self):
        self.keys = [
            k for k in [
                settings.gemini_api_key_1,
                settings.gemini_api_key_2,
                settings.gemini_api_key_3,
                settings.gemini_api_key_4,
            ] if k
        ]
        if not self.keys:
            raise ValueError("No Gemini API keys configured - set at least GEMINI_API_KEY_1 in .env")
        self.index = 0

    def current_key(self) -> str:
        return self.keys[self.index]

    def rotate(self) -> str:
        self.index = (self.index + 1) % len(self.keys)
        print(f"[Gemini] Quota hit - rotating to API key #{self.index + 1}/{len(self.keys)}")
        return self.current_key()


_key_rotator = GeminiKeyRotator()
MODEL_ID = "gemini-2.5-flash"

import httpx # Make sure this is imported at the top


# ==============================================================================
# FUNCTION DECLARATIONS (Tools)
# ==============================================================================
FUNCTION_DECLARATIONS = [
    types.FunctionDeclaration(
        name="get_or_create_user",
        description="Lookup or create a user by their registered phone number. Use this first.",
        parameters={
            "type": "object",
            "properties": {"whatsapp_number": {"type": "string", "description": "The user's phone number."}},
            "required": ["whatsapp_number"],
        }
    ),
    types.FunctionDeclaration(
        name="create_transaction",
        description="Initiates a new escrow deal between a buyer and seller.",
        parameters={
            "type": "object",
            "properties": {
                "initiator_id": {"type": "string"},
                "counterparty_id": {"type": "string"},
                "buyer_id": {"type": "string"},
                "seller_id": {"type": "string"},
                "item_description": {"type": "string"},
                "amount": {"type": "number"},
                "currency": {"type": "string", "default": "NGN"}
            },
            "required": ["initiator_id", "counterparty_id", "buyer_id", "seller_id", "item_description", "amount"]
        }
    ),
    types.FunctionDeclaration(
        name="get_transaction_by_ref",
        description="Retrieves transaction details using the OPUS-XXXX reference.",
        parameters={
            "type": "object",
            "properties": {"transaction_ref": {"type": "string"}},
            "required": ["transaction_ref"]
        }
    ),
    types.FunctionDeclaration(
        name="accept_transaction",
        description="Accepts a pending escrow transaction.",
        parameters={
            "type": "object",
            "properties": {"transaction_ref": {"type": "string"}},
            "required": ["transaction_ref"]
        }
    ),
    types.FunctionDeclaration(
        name="generate_payment_account",
        description="Generates a Nomba virtual account for the buyer to pay into.",
        parameters={
            "type": "object",
            "properties": {"transaction_ref": {"type": "string"}},
            "required": ["transaction_ref"]
        }
    ),
    types.FunctionDeclaration(
        name="mark_delivered",
        description="Signals that the seller has delivered the goods/service.",
        parameters={
            "type": "object",
            "properties": {"transaction_ref": {"type": "string"}},
            "required": ["transaction_ref"]
        }
    ),
    types.FunctionDeclaration(
        name="initiate_payout",
        description="Triggers the Nomba transfer to the seller. Use after buyer confirmation.",
        parameters={
            "type": "object",
            "properties": {
                "transaction_ref": {"type": "string"},
                "account_number": {"type": "string"},
                "bank_code": {"type": "string"},
                "account_name": {"type": "string"},
                "opus_fee": {"type": "number"},
            },
            "required": ["transaction_ref", "account_number", "bank_code", "account_name", "opus_fee"]
        }
    ),
    types.FunctionDeclaration(
        name="raise_dispute",
        description="Freezes funds and flags the transaction for manual review.",
        parameters={
            "type": "object",
            "properties": {
                "transaction_ref": {"type": "string"},
                "reason": {"type": "string"},
                "actor": {"type": "string"}
            },
            "required": ["transaction_ref", "reason", "actor"]
        }
    ),
    types.FunctionDeclaration(
        name="send_telegram_message",
        description="Sends a message to a user via Telegram. Use this to notify counterparties, send transaction refs, or provide payment details.",
        parameters={
            "type": "object",
            "properties": {
                "to_number": {"type": "string", "description": "The recipient's registered phone number."},
                "message": {"type": "string", "description": "The text content of the message."},
            },
            "required": ["to_number", "message"],
        },
    ),
    types.FunctionDeclaration(
    name="update_profile",
    description="Saves a user's onboarding details (name, location).",
    parameters={
        "type": "object",
        "properties": {
            "user_id": {"type": "string"},
            "first_name": {"type": "string"},
            "last_name": {"type": "string"},
            "location": {"type": "string"},
        },
        "required": ["user_id"],
    },
),
types.FunctionDeclaration(
    name="request_verification",
    description="Submits a user's BVN for identity verification. Only call once the user has explicitly provided their BVN.",
    parameters={
        "type": "object",
        "properties": {
            "user_id": {"type": "string"},
            "method": {"type": "string", "description": "Always 'bvn' for now."},
            "verification_number": {"type": "string"},
        },
        "required": ["user_id", "method", "verification_number"],
    },
),
types.FunctionDeclaration(
    name="decline_transaction",
    description="Declines a pending escrow invitation.",
    parameters={
        "type": "object",
        "properties": {"transaction_ref": {"type": "string"}},
        "required": ["transaction_ref"],
    },
),
types.FunctionDeclaration(
    name="confirm_payout",
    description="Checks whether a payout to the seller has finished processing. Call this after initiate_payout to check final status.",
    parameters={
        "type": "object",
        "properties": {"transaction_ref": {"type": "string"}},
        "required": ["transaction_ref"],
    },
),
]

# ==============================================================================
# DISPATCH TABLE
# ==============================================================================
FUNCTION_MAP = {
    "get_or_create_user": get_or_create_user,
    "update_profile": update_profile,
    "request_verification": request_verification,
    "create_transaction": create_transaction,
    "get_transaction_by_ref": get_transaction_by_ref,
    "accept_transaction": accept_transaction,
    "decline_transaction": decline_transaction,
    "mark_delivered": mark_delivered,
    "raise_dispute": raise_dispute,
    "generate_payment_account": generate_payment_account,
    "initiate_payout": initiate_payout,
    "send_telegram_message": send_telegram_message_by_phone,
    "confirm_payout": confirm_payout,
}

# ==============================================================================
# GEMINI AI CLASS
# ==============================================================================
class GeminiLLM:
    def __init__(self):
        self._build_client_and_chat()

    def _build_client_and_chat(self) -> None:
        self.client = genai.Client(api_key=_key_rotator.current_key())
        tool = types.Tool(function_declarations=FUNCTION_DECLARATIONS)
        self.chat = self.client.aio.chats.create(
            model=MODEL_ID,
            config=types.GenerateContentConfig(
                tools=[tool],
                temperature=0.3,
                system_instruction=(
                    "You are Opus Escrow AI, a secure escrow assistant. Identify every "
                    "user by their registered phone number - never assume a specific "
                    "messaging platform.\n\n"
                    "MANDATORY FLOW - do not skip steps:\n"
                    "1. Get the user's phone number and call get_or_create_user.\n"
                    "2. If they're new or missing profile details, collect first name, "
                    "last name, and location, then call update_profile.\n"
                    "3. If verification_status isn't 'verified', ask for their BVN and "
                    "call request_verification with method='bvn'. Do NOT proceed to any "
                    "transaction step until verification_status is 'verified'.\n"
                    "4. Once both parties are verified, help create or respond to escrow "
                    "transactions using transaction references (OPUS-XXXXXX).\n"
                    "5. When a transaction is created, proactively call "
                    "send_telegram_message to notify the counterparty with the "
                    "transaction reference and instructions to accept.\n"
                    "6. Continue the flow: accept -> generate payment account -> "
                    "delivery -> payout confirmation, always using the transaction ref."
                )
            )
        )

    async def _resolve_ref_to_id(self, ref: str) -> ObjectId:
        tx = await get_transaction_by_ref(ref)
        if not tx:
            raise ValueError(f"Transaction reference {ref} not found.")
        return tx["_id"]

    def _is_quota_error(self, exc: Exception) -> bool:
        return "RESOURCE_EXHAUSTED" in str(exc) or "429" in str(exc)

    async def send(self, message: str) -> str:
        try:
            response = await self.chat.send_message(message)
        except Exception as exc:
            if self._is_quota_error(exc):
                _key_rotator.rotate()
                self._build_client_and_chat()  # fresh chat - see note on lost history
                response = await self.chat.send_message(message)
            else:
                raise

        while True:
            function_calls = [part.function_call for part in response.candidates[0].content.parts if part.function_call]

            if not function_calls:
                return response.text

            tool_responses = []
            for fc in function_calls:
                result = await self._execute_function(fc.name, fc.args)
                tool_responses.append(types.Part.from_function_response(name=fc.name, response=result))

            try:
                response = await self.chat.send_message(tool_responses)
            except Exception as exc:
                if self._is_quota_error(exc):
                    _key_rotator.rotate()
                    self._build_client_and_chat()
                    # tool_responses can't be replayed into a fresh chat with no
                    # matching function_call turn - ask the user to repeat instead
                    return "I hit a temporary limit mid-response - please repeat your last message."
                raise

    async def _execute_function(self, name: str, fc_args: Any) -> Dict[str, Any]:
        print(f"\n[TOOL CALL] {name}({fc_args})")
        
        if name not in FUNCTION_MAP:
            return {"error": f"Function {name} not found"}

        try:
            # 1. ALWAYS CREATE A NEW DICT. Do not modify fc_args directly.
            args = dict(fc_args) 

            # 2. Bridge transaction_ref to transaction_id
            if "transaction_ref" in args and name != "get_transaction_by_ref":
                ref = args.pop("transaction_ref")
                args["transaction_id"] = await self._resolve_ref_to_id(ref)

            # 3. Convert string IDs to ObjectIds for the database
            for key in ["initiator_id", "counterparty_id", "buyer_id", "seller_id", "user_id"]:
                if key in args and isinstance(args[key], str):
                    try:
                        args[key] = ObjectId(args[key])
                    except Exception:
                        print(f"Warning: {key} '{args[key]}' is not a valid ObjectId")

            # 4. Execute repository function
            result = await FUNCTION_MAP[name](**args)

            # 5. Deep Clean the result before returning to Gemini
            # This handles nested ObjectIds and Datetimes in the DB response
            return self._serialize_bson(result)

        except Exception as e:
            print(f"[TOOL ERROR] {str(e)}")
            return {"error": str(e)}

    def _serialize_bson(self, data: Any) -> Any:
        """Recursively converts BSON types (ObjectId, datetime) to JSON-safe types."""
        if isinstance(data, list):
            return [self._serialize_bson(i) for i in data]
        elif isinstance(data, dict):
            return {k: self._serialize_bson(v) for k, v in data.items()}
        elif isinstance(data, ObjectId):
            return str(data)
        elif hasattr(data, "isoformat"): # Handles datetime
            return data.isoformat()
        return data

# ==============================================================================
# ENTRY POINT
# ==============================================================================
async def main():
    llm = GeminiLLM()
    print("-" * 50)
    print("Opus Escrow AI (Gemini 2.5 Flash) - Async Mode")
    print("-" * 50)

    while True:
        try:
            user_input = input("\nYou: ")
            if user_input.lower() in ["exit", "quit"]:
                break
            
            reply = await llm.send(user_input)
            print(f"\nAssistant: {reply}")

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"\n[System Error]: {e}")

if __name__ == "__main__":
    asyncio.run(main())