"""
Prembly identity verification integration - BVN validation only for now.

Docs: https://docs.prembly.com/docs/bvn-basic-copy
Endpoint: POST https://api.prembly.com/verification/bvn_validation

IMPORTANT: this function receives the raw BVN and sends it directly to
Prembly. It must never log, persist, or return the raw number - only the
success/failure result gets passed back up.
"""

import uuid

import httpx

from opus_escrow.config import get_settings


class PremblyAPIError(Exception):
    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"Prembly API error {status_code}: {body}")


async def verify_bvn(bvn: str) -> dict:
    """
    Calls Prembly's BVN Basic endpoint.

    NOTE: Prembly's current docs state auth uses only the Secret Key,
    with no other Authorization header needed - unlike older SDK
    examples that used separate app-id/x-api-key headers. The exact
    header format below (raw key, no "Bearer" prefix) is our best
    reading of their docs - if this 401s, check the raw response body
    for a clearer hint and adjust.
    """
    settings = get_settings()
    headers = {
        "x-api-key": settings.prembly_secret_key,
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.prembly.com/verification/bvn_validation",
            headers=headers,
            json={"number": bvn},
        )

    if response.status_code != 200:
        raise PremblyAPIError(response.status_code, response.text)

    data = response.json()
    success = bool(data.get("status")) and data.get("response_code") == "00"

    return {
        "success": success,
        "reference": str(uuid.uuid4()),
        "raw_detail": data.get("detail", ""),
    }