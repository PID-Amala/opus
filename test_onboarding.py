"""
Manual test script for the onboarding flow. Run from the project root:

    python test_onboarding.py

Requires VERIFICATION_MOCK_MODE=true in .env until the real verification
API is wired up.
"""

import asyncio

from opus_escrow.db.client import close_client
from opus_escrow.repositories.users import get_or_create_user, request_verification, update_profile


async def main() -> None:
    test_number = "2348000000000"

    print(f"1. get_or_create_user({test_number!r})")
    user = await get_or_create_user(test_number)
    print(f"   -> {user}\n")

    print("2. update_profile(first_name, last_name, location)")
    await update_profile(
        user["_id"],
        first_name="Test",
        last_name="User",
        location="Lagos",
    )
    print("   -> updated\n")

    print("3. request_verification(method='bvn', verification_number='12345678901')")
    updated_user = await request_verification(user["_id"], method="bvn", verification_number="12345678901")
    print(f"   -> {updated_user}\n")

    print("Done. verification_status should read 'verified' (mock mode).")
    await close_client()


if __name__ == "__main__":
    asyncio.run(main())