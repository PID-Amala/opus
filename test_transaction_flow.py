"""
Manual test script for the transaction lifecycle - now wired to real
Nomba sandbox calls for virtual account generation and payout. Run from
the project root:

    python test_transaction_flow.py
"""

import asyncio

from opus_escrow.db.client import close_client
from opus_escrow.integrations.nomba import lookup_bank_account
from opus_escrow.repositories.transactions import (
    accept_transaction,
    confirm_payout,
    create_transaction,
    generate_payment_account,
    initiate_payout,
    mark_delivered,
    mark_funds_held,
)
from opus_escrow.repositories.users import get_or_create_user


async def main() -> None:
    print("0. Setting up two test users (initiator + counterparty)")
    initiator = await get_or_create_user("2348000000001")
    counterparty = await get_or_create_user("2348000000002")
    print(f"   initiator:    {initiator['_id']}")
    print(f"   counterparty: {counterparty['_id']}\n")

    print("1. create_transaction(...)")
    txn = await create_transaction(
        initiator_id=initiator["_id"],
        counterparty_id=counterparty["_id"],
        buyer_id=initiator["_id"],
        seller_id=counterparty["_id"],
        item_description="iPhone 13 Pro, 256GB",
        amount=450000.00,
    )
    print(f"   ref: {txn['transaction_ref']}  status: {txn['status']}\n")

    print("2. accept_transaction(...)")
    txn = await accept_transaction(txn["_id"])
    print(f"   status: {txn['status']}  buyer_id: {txn['buyer_id']}  seller_id: {txn['seller_id']}\n")

    print("3. generate_payment_account(...) - real Nomba sandbox call")
    txn = await generate_payment_account(txn["_id"])
    va = txn["payment"]["virtual_account"]
    print(f"   virtual account: {va['bankAccountNumber']} ({va['bankName']})\n")

    print("4. mark_funds_held(...) - simulating a confirmed webhook (can't test the real webhook yet)")
    txn = await mark_funds_held(txn["_id"], nomba_reference="TEST-NOMBA-REF-001", webhook_payload={"test": True})
    print(f"   status: {txn['status']}\n")

    print("5. mark_delivered(...)")
    txn = await mark_delivered(txn["_id"])
    print(f"   status: {txn['status']}  delivered_at: {txn['timestamps'].get('delivered_at')}\n")

    print("6. Looking up a real sandbox seller bank account...")
    seller_account = await lookup_bank_account(account_number="0554772814", bank_code="053")
    print(f"   -> {seller_account}\n")

    print("7. initiate_payout(...) - real Nomba sandbox transfer")
    txn = await initiate_payout(
        txn["_id"],
        account_number=seller_account["accountNumber"],
        bank_code="053",
        account_name=seller_account["accountName"],
        opus_fee=4500.00,
    )
    print(f"   status: {txn['status']}\n")

    print("8. confirm_payout(...) - requerying Nomba for final status")
    txn = await confirm_payout(txn["_id"])
    print(f"   status: {txn['status']}\n")

    print(f"Done. Final transaction_ref: {txn['transaction_ref']}, final status: {txn['status']}")
    if txn["status"] != "completed":
        print("Transfer likely still processing on Nomba's side (can take up to ~3 min per their docs).")
        print("Re-run confirm_payout() manually later, or check via a follow-up script.")

    await close_client()


if __name__ == "__main__":
    asyncio.run(main())