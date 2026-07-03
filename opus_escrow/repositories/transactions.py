"""
Transactions repository - creation and every state transition shown in
the escrow flowchart. Every transition goes through _transition(), which
writes the from/to status change to transaction_logs automatically AND
enforces that the transaction is actually in a legal predecessor state
before anything happens - this is the backend validation layer that
Gemini's function-calling sits on top of. Gemini can propose actions;
only a transaction already in the right state will actually move.

Plain async functions, no FastAPI dependency, so these are callable from
HTTP routes today and from Gemini function-calling handlers later.
"""

import secrets
import string
from datetime import datetime, timezone
from typing import Any, Optional
from opus_escrow.repositories.users import get_user_by_id

from bson import ObjectId

from opus_escrow.db.client import get_database
from opus_escrow.integrations import nomba


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _generate_transaction_ref() -> str:
    suffix = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
    return f"OPUS-{suffix}"


class InvalidTransactionStateError(Exception):
    """
    Raised when an action is attempted on a transaction that isn't in a
    legal predecessor state for that action - e.g. trying to pay out a
    transaction that was never marked funds_held. This is the safety net
    that stops Gemini (or anything else) from moving money based on a
    misread conversation rather than actual verified state.
    """

    def __init__(self, action: str, current_status: str, allowed: set):
        self.action = action
        self.current_status = current_status
        self.allowed = allowed
        super().__init__(
            f"Cannot {action}: transaction is in status '{current_status}', "
            f"but this action requires one of {sorted(allowed)}."
        )


def _require_status(current: dict, action: str, allowed: set) -> None:
    if current["status"] not in allowed:
        raise InvalidTransactionStateError(action, current["status"], allowed)


async def get_transaction(transaction_id: ObjectId) -> Optional[dict]:
    db = get_database()
    return await db.transactions.find_one({"_id": transaction_id})


async def get_transaction_by_ref(transaction_ref: str) -> Optional[dict]:
    db = get_database()
    return await db.transactions.find_one({"transaction_ref": transaction_ref})


async def create_transaction(
    initiator_id: ObjectId,
    counterparty_id: ObjectId,
    buyer_id: ObjectId,
    seller_id: ObjectId,
    item_description: str,
    amount: float,
    currency: str = "NGN",
) -> dict:
    """
    Corresponds to START_ESCROW_TRANSACTION -> STATUS_PENDING_ACCEPTANCE.
    buyer_id/seller_id are already known at this point - ASK_WHO_IS_PAYING
    happens during initiation, before the invite is even sent.
    """
    db = get_database()
    buyer = await get_user_by_id(buyer_id)
    seller = await get_user_by_id(seller_id)
    if not buyer or buyer.get("verification_status") != "verified":
        raise ValueError("Buyer must complete identity verification before a transaction can be created.")
    if not seller or seller.get("verification_status") != "verified":
        raise ValueError("Seller must complete identity verification before a transaction can be created.")
    now = _now()
    transaction_ref = _generate_transaction_ref()

    doc = {
        "transaction_ref": transaction_ref,
        "status": "pending_acceptance",
        "initiator_id": initiator_id,
        "counterparty_id": counterparty_id,
        "buyer_id": buyer_id,
        "seller_id": seller_id,
        "item_description": item_description,
        "amount": float(amount),
        "currency": currency,
        "opus_fee": 0.0,
        "payment": {},
        "timestamps": {},
        "created_at": now,
        "updated_at": now,
    }
    result = await db.transactions.insert_one(doc)
    doc["_id"] = result.inserted_id

    await log_transaction_event(
        result.inserted_id,
        from_status=None,
        to_status="pending_acceptance",
        actor="system",
        event_type="transaction_created",
        metadata={"transaction_ref": transaction_ref},
    )
    return doc


def _resolve_counterparty_role(current: dict) -> str:
    """The counterparty was assigned buyer or seller at creation time - look it up."""
    return "buyer" if current["counterparty_id"] == current["buyer_id"] else "seller"


async def accept_transaction(transaction_id: ObjectId) -> dict:
    """YES_ACCEPTANCE -> STATUS_AWAITING_PAYMENT. Requires pending_acceptance."""
    current = await get_transaction(transaction_id)
    if current is None:
        raise ValueError(f"Transaction {transaction_id} not found")
    _require_status(current, "accept_transaction", {"pending_acceptance"})

    return await _transition(
        transaction_id,
        to_status="awaiting_payment",
        actor=_resolve_counterparty_role(current),
        event_type="acceptance_confirmed",
        extra_fields={"timestamps.accepted_at": _now()},
    )


async def decline_transaction(transaction_id: ObjectId) -> dict:
    """NO_ACCEPTANCE -> STATUS_CANCELLED. Requires pending_acceptance."""
    current = await get_transaction(transaction_id)
    if current is None:
        raise ValueError(f"Transaction {transaction_id} not found")
    _require_status(current, "decline_transaction", {"pending_acceptance"})

    return await _transition(
        transaction_id,
        to_status="cancelled",
        actor=_resolve_counterparty_role(current),
        event_type="acceptance_declined",
    )


async def generate_payment_account(transaction_id: ObjectId) -> dict:
    """
    GENERATE_VIRTUAL_ACCOUNT_OR_PAYMENT_LINK. Requires awaiting_payment.
    Status check happens BEFORE the Nomba call, not after - we never want
    to create a real virtual account for a transaction that isn't
    actually in a state that should have one.
    """
    current = await get_transaction(transaction_id)
    if current is None:
        raise ValueError(f"Transaction {transaction_id} not found")
    _require_status(current, "generate_payment_account", {"awaiting_payment"})

    account = await nomba.create_virtual_account(
        account_ref=current["transaction_ref"],
        account_name=f"Opus Escrow - {current['transaction_ref']}",
        expected_amount=current["amount"],
    )

    db = get_database()
    await db.transactions.update_one(
        {"_id": transaction_id},
        {"$set": {"payment.virtual_account": account, "updated_at": _now()}},
    )
    await log_transaction_event(
        transaction_id,
        from_status="awaiting_payment",
        to_status="awaiting_payment",
        actor="system",
        event_type="virtual_account_generated",
        metadata={"accountRef": account.get("accountRef")},
    )
    return await get_transaction(transaction_id)


async def mark_funds_held(transaction_id: ObjectId, nomba_reference: str, webhook_payload: dict) -> dict:
    """
    YES_PAYMENT_RECEIVED -> STATUS_FUNDS_HELD. Requires awaiting_payment.

    IMPORTANT: this function must ONLY ever be called by the real Nomba
    webhook handler (once deployed) or a manual dev test script - it must
    NEVER be exposed as a Gemini-callable tool. Doing so would let a
    conversation "claim" payment happened with no actual proof, which
    defeats the entire point of verifying against Nomba's webhook.
    """
    current = await get_transaction(transaction_id)
    if current is None:
        raise ValueError(f"Transaction {transaction_id} not found")
    _require_status(current, "mark_funds_held", {"awaiting_payment"})

    return await _transition(
        transaction_id,
        to_status="funds_held",
        actor="system",
        event_type="payment_confirmed",
        extra_fields={
            "payment.nomba_reference": nomba_reference,
            "payment.webhook_payload": webhook_payload,
            "timestamps.paid_at": _now(),
        },
    )


async def mark_expired(transaction_id: ObjectId) -> dict:
    """NO_PAYMENT_RECEIVED -> WAIT_UNTIL_PAYMENT_TIMEOUT -> STATUS_EXPIRED. Requires awaiting_payment."""
    current = await get_transaction(transaction_id)
    if current is None:
        raise ValueError(f"Transaction {transaction_id} not found")
    _require_status(current, "mark_expired", {"awaiting_payment"})

    return await _transition(
        transaction_id,
        to_status="expired",
        actor="system",
        event_type="payment_timeout",
    )


async def mark_delivered(transaction_id: ObjectId) -> dict:
    """
    SELLER_DELIVERS_ITEM_SERVICE. Requires funds_held. Doesn't change
    status - buyer confirmation is the next gate - but records the event.
    """
    current = await get_transaction(transaction_id)
    if current is None:
        raise ValueError(f"Transaction {transaction_id} not found")
    _require_status(current, "mark_delivered", {"funds_held"})

    db = get_database()
    await db.transactions.update_one(
        {"_id": transaction_id}, {"$set": {"timestamps.delivered_at": _now(), "updated_at": _now()}}
    )
    await log_transaction_event(
        transaction_id, from_status="funds_held", to_status="funds_held", actor="seller", event_type="seller_delivered"
    )
    return await get_transaction(transaction_id)


async def raise_dispute(transaction_id: ObjectId, actor: str, reason: str) -> dict:
    """NO_BUYER_CONFIRMED / NO_RESPONSE_AFTER_X_DAYS -> STATUS_DISPUTED. Requires funds_held."""
    current = await get_transaction(transaction_id)
    if current is None:
        raise ValueError(f"Transaction {transaction_id} not found")
    _require_status(current, "raise_dispute", {"funds_held"})

    return await _transition(
        transaction_id,
        to_status="disputed",
        actor=actor,
        event_type="dispute_raised",
        extra_fields={"timestamps.disputed_at": _now()},
        metadata={"reason": reason},
    )


async def initiate_payout(
    transaction_id: ObjectId,
    account_number: str,
    bank_code: str,
    account_name: str,
    opus_fee: float,
) -> dict:
    """
    YES_BUYER_CONFIRMED -> CALL_NOMBA_TRANSFER_API. Requires funds_held -
    checked BEFORE the real transfer call, not after, so we never fire
    money at Nomba for a transaction that hasn't actually confirmed
    payment was received. Moves to payout_processing, not completed -
    transfers are async, confirm_payout() finalizes it.
    """
    current = await get_transaction(transaction_id)
    if current is None:
        raise ValueError(f"Transaction {transaction_id} not found")
    _require_status(current, "initiate_payout", {"funds_held"})

    transfer_amount = round(current["amount"] - opus_fee, 2)
    merchant_tx_ref = f"{current['transaction_ref']}-PAYOUT"

    transfer = await nomba.transfer_to_bank(
        amount=transfer_amount,
        account_number=account_number,
        bank_code=bank_code,
        merchant_tx_ref=merchant_tx_ref,
        account_name=account_name,
        sender_name="Opus Escrow",
        narration=f"Opus Escrow payout - {current['transaction_ref']}",
    )

    return await _transition(
        transaction_id,
        to_status="payout_processing",
        actor="system",
        event_type="payout_initiated",
        extra_fields={"opus_fee": float(opus_fee), "payment.transfer": transfer},
        metadata={"transfer_amount": transfer_amount, "sessionId": transfer.get("meta", {}).get("sessionId")},
    )


async def confirm_payout(transaction_id: ObjectId) -> dict:
    """
    Requeries Nomba for the transfer's final status. Requires
    payout_processing. Safe to expose to Gemini - it can't fabricate
    success, only Nomba's own response can move this to completed.
    """
    current = await get_transaction(transaction_id)
    if current is None:
        raise ValueError(f"Transaction {transaction_id} not found")
    _require_status(current, "confirm_payout", {"payout_processing"})

    session_id = current.get("payment", {}).get("transfer", {}).get("meta", {}).get("sessionId")
    if not session_id:
        raise ValueError("No transfer sessionId found on this transaction - was initiate_payout() called?")

    status_result = await nomba.requery_transaction(session_id)
    final_status = status_result.get("status")

    if final_status == "SUCCESS":
        return await _transition(
            transaction_id,
            to_status="completed",
            actor="system",
            event_type="funds_released",
            extra_fields={"timestamps.completed_at": _now(), "payment.transfer_confirmation": status_result},
        )

    await log_transaction_event(
        transaction_id,
        from_status="payout_processing",
        to_status="payout_processing",
        actor="system",
        event_type="payout_status_checked",
        metadata={"status": final_status},
    )
    return await get_transaction(transaction_id)


async def _transition(
    transaction_id: ObjectId,
    to_status: str,
    actor: str,
    event_type: str,
    extra_fields: Optional[dict] = None,
    metadata: Optional[dict] = None,
) -> dict:
    db = get_database()
    current = await get_transaction(transaction_id)
    if current is None:
        raise ValueError(f"Transaction {transaction_id} not found")
    from_status = current["status"]

    update: dict[str, Any] = {"status": to_status, "updated_at": _now()}
    if extra_fields:
        update.update(extra_fields)

    await db.transactions.update_one({"_id": transaction_id}, {"$set": update})

    await log_transaction_event(
        transaction_id,
        from_status=from_status,
        to_status=to_status,
        actor=actor,
        event_type=event_type,
        metadata=metadata,
    )
    return await get_transaction(transaction_id)


async def log_transaction_event(
    transaction_id: ObjectId,
    from_status: Optional[str],
    to_status: str,
    actor: str,
    event_type: str,
    metadata: Optional[dict] = None,
) -> None:
    db = get_database()
    doc: dict[str, Any] = {
        "transaction_id": transaction_id,
        "to_status": to_status,
        "actor": actor,
        "event_type": event_type,
        "metadata": metadata or {},
        "created_at": _now(),
    }
    if from_status is not None:
        doc["from_status"] = from_status
    await db.transaction_logs.insert_one(doc)

async def get_user_by_telegram_chat_id(chat_id: str) -> Optional[dict]:
    db = get_database()
    return await db.users.find_one({"telegram_chat_id": chat_id})