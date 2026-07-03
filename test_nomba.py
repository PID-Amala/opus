"""
Manual test script for the real Nomba API integration (everything except
webhooks, which need a deployed URL to test). Run from the project root:

    python test_nomba.py

Requires NOMBA_CLIENT_ID, NOMBA_CLIENT_SECRET, NOMBA_ACCOUNT_ID in .env.
This makes real calls to Nomba's API - use test/sandbox credentials if
Nomba provides them, check your dashboard before running against live.
"""

import asyncio
import uuid

from opus_escrow.integrations.nomba import (
    NombaAPIError,
    _get_access_token,
    create_virtual_account,
    fetch_bank_codes,
    fetch_virtual_account,
    lookup_bank_account,
    transfer_to_bank,
)


async def main() -> None:
    print("1. Fetching access token...")
    try:
        token = await _get_access_token()
        print(f"   -> got token, length {len(token)} chars: {token[:20]}...\n")
    except NombaAPIError as exc:
        print(f"   -> FAILED: {exc}\n")
        return
    except Exception as exc:
        print(f"   -> FAILED (unexpected shape - check the response manually): {exc}\n")
        return

    print("2. Fetching bank codes (read-only, safe call)...")
    try:
        banks = await fetch_bank_codes()
        print(f"   -> got {len(banks)} banks. First few: {[b['name'] for b in banks[:3]]}\n")
    except NombaAPIError as exc:
        print(f"   -> FAILED: {exc}\n")

    print("3. Creating a test virtual account...")
    account_ref = f"TEST-{uuid.uuid4().hex[:12]}"
    try:
        account = await create_virtual_account(
            account_ref=account_ref,
            account_name="Opus Escrow Test",
            expected_amount=100.00,
        )
        print(f"   -> created: {account}\n")
    except NombaAPIError as exc:
        print(f"   -> FAILED: {exc}\n")
        return

    print("4. Fetching that same virtual account back...")
    try:
        fetched = await fetch_virtual_account(account_ref)
        print(f"   -> fetched: {fetched}\n")
    except NombaAPIError as exc:
        print(f"   -> FAILED: {exc}\n")

    print("5. Looking up a test bank account...")
    try:
        lookup = await lookup_bank_account(account_number="0554772814", bank_code="053")
        print(f"   -> {lookup}\n")
    except NombaAPIError as exc:
        print(f"   -> FAILED: {exc}\n")

    print("6. Making a test transfer (using the account we just verified in step 5)...")
    try:
        transfer = await transfer_to_bank(
            amount=3500,
            account_number="0554772814",
            bank_code="053",
            merchant_tx_ref=f"TEST-TX-{uuid.uuid4().hex[:8]}",
            account_name="Lucas Mia",
            sender_name="Opus Escrow Test",
            narration="Test transfer",
        )
        print(f"   -> {transfer}\n")
    except NombaAPIError as exc:
        print(f"   -> FAILED: {exc}\n")


if __name__ == "__main__":
    asyncio.run(main())