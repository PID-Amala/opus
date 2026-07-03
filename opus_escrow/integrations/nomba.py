import base64
import hashlib
import hmac
import time
from datetime import datetime
from typing import Any, Optional

import httpx

from opus_escrow.config import get_settings

_cached_token: Optional[str] = None
_cached_token_expires_at: float = 0.0
_TOKEN_TTL_FALLBACK_SECONDS = 55 * 60  # only used if expiresAt is ever missing from the response


class NombaAPIError(Exception):
    def __init__(self, status_code: int, body: Any):
        self.status_code = status_code
        self.body = body
        super().__init__(f"Nomba API error {status_code}: {body}")


async def _get_access_token(force_refresh: bool = False) -> str:
    global _cached_token, _cached_token_expires_at

    now = time.time()
    if not force_refresh and _cached_token and now < _cached_token_expires_at:
        return _cached_token

    settings = get_settings()
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{settings.nomba_base_url}/v1/auth/token/issue",
            headers={"Content-Type": "application/json", "accountId": settings.nomba_account_id},
            json={
                "grant_type": "client_credentials",
                "client_id": settings.nomba_client_id,
                "client_secret": settings.nomba_client_secret,
            },
        )
    if response.status_code != 200:
        raise NombaAPIError(response.status_code, response.text)

    body = response.json()
    # Real shape confirmed from a live call: {"code": "00", "data": {"access_token": "...", "expiresAt": "..."}}
    data = body.get("data", {})
    token = data.get("access_token")
    if not token:
        raise NombaAPIError(response.status_code, f"Unexpected token response shape: {body}")

    expires_at_str = data.get("expiresAt")
    if expires_at_str:
        try:
            expires_dt = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
            _cached_token_expires_at = expires_dt.timestamp() - 60  # 60s safety buffer
        except ValueError:
            _cached_token_expires_at = now + _TOKEN_TTL_FALLBACK_SECONDS
    else:
        _cached_token_expires_at = now + _TOKEN_TTL_FALLBACK_SECONDS

    _cached_token = token
    return token


async def _request(method: str, path: str, json: Optional[dict] = None, base_url: Optional[str] = None) -> dict:
    """
    Makes an authenticated Nomba API call. Retries once on 401 in case the
    cached token expired - this is the real safety net since we don't
    have a confirmed token TTL from the docs.
    """
    settings = get_settings()
    url = f"{base_url or settings.nomba_base_url}{path}"

    for attempt in (1, 2):
        token = await _get_access_token(force_refresh=(attempt == 2))
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "accountId": settings.nomba_account_id,
        }
        async with httpx.AsyncClient() as client:
            response = await client.request(method, url, headers=headers, json=json)

        if response.status_code == 401 and attempt == 1:
            continue  # retry once with a forced-fresh token
        if response.status_code >= 400:
            raise NombaAPIError(response.status_code, response.text)
        return response.json()

    raise NombaAPIError(response.status_code, response.text)


async def create_virtual_account(
    account_ref: str,
    account_name: str,
    expected_amount: float,
    bvn: Optional[str] = None,
    expiry_date: Optional[str] = None,
) -> dict:
    """
    Generates a dedicated virtual account for a single transaction's
    payment collection. Returns Nomba's `data` object, which includes
    bankName, bankAccountNumber, bankAccountName, accountRef.
    """
    payload: dict[str, Any] = {
        "accountRef": account_ref,
        "accountName": account_name,
        "expectedAmount": f"{expected_amount:.2f}",
    }
    if bvn:
        payload["bvn"] = bvn
    if expiry_date:
        payload["expiryDate"] = expiry_date

    result = await _request("POST", "/v1/accounts/virtual", json=payload)
    return result["data"]


async def fetch_virtual_account(account_ref: str) -> dict:
    result = await _request("GET", f"/v1/accounts/virtual/{account_ref}")
    return result["data"]


async def lookup_bank_account(account_number: str, bank_code: str) -> dict:
    """Returns {"accountNumber": ..., "accountName": ...} for display/confirmation before a payout."""
    result = await _request(
        "POST", "/v1/transfers/bank/lookup", json={"accountNumber": account_number, "bankCode": bank_code}
    )
    return result["data"]


async def fetch_bank_codes() -> list[dict]:
    result = await _request("GET", "/v1/transfers/bank")
    return result["data"]


async def transfer_to_bank(
    amount: float,
    account_number: str,
    bank_code: str,
    merchant_tx_ref: str,
    account_name: str = "",
    sender_name: str = "",
    narration: str = "",
) -> dict:
    """
    Pays a seller out to their bank account. This is what
    complete_transaction() in the transactions repository should call
    before recording the transaction as completed.
    """
    settings = get_settings()
    payload = {
        "amount": amount,
        "accountNumber": account_number,
        "bankCode": bank_code,
        "merchantTxRef": merchant_tx_ref,
        "accountName": account_name,
        "senderName": sender_name,
        "narration": narration,
    }
    result = await _request("POST", "/v2/transfers/bank", json=payload, base_url=settings.nomba_base_url)
    return result["data"]

async def requery_transaction(session_id: str) -> dict:
    """
    Confirms the final status of a transfer via its sessionId (found in
    the transfer response's meta.sessionId). Nomba's docs are explicit:
    do NOT assume success from the initial transfer response alone -
    PENDING_BILLING means "accepted but not yet finalized." Poll this
    (with backoff, up to ~3 minutes per their docs) or wait for the
    webhook once that's wired up.
    """
    result = await _request("GET", f"/v1/transactions/requery/{session_id}")
    return result["data"]

def verify_webhook_signature(raw_body_fields: dict, timestamp: str, received_signature: str) -> bool:
    """
    Verifies a Nomba webhook per their documented HMAC scheme:

        hashing_payload = "{event_type}:{requestId}:{merchant.userId}:
                            {merchant.walletId}:{transaction.transactionId}:
                            {transaction.type}:{transaction.time}:
                            {transaction.responseCode}"
        message = f"{hashing_payload}:{timestamp}"
        digest = base64(HMAC-SHA256(signature_key, message))

    raw_body_fields: the parsed webhook JSON body.
    timestamp: value from whatever header carries it (header name TBD -
               see module docstring).
    received_signature: value from the signature header (header name TBD).

    Uses constant-time comparison to avoid timing attacks.
    """
    settings = get_settings()

    try:
        merchant = raw_body_fields["data"]["merchant"]
        transaction = raw_body_fields["data"]["transaction"]
        hashing_payload = ":".join(
            [
                str(raw_body_fields["event_type"]),
                str(raw_body_fields["requestId"]),
                str(merchant["userId"]),
                str(merchant["walletId"]),
                str(transaction["transactionId"]),
                str(transaction["type"]),
                str(transaction["time"]),
                str(transaction.get("responseCode", "")),
            ]
        )
    except KeyError as exc:
        raise ValueError(f"Webhook payload missing expected field: {exc}") from exc

    message = f"{hashing_payload}:{timestamp}"
    computed = hmac.new(settings.nomba_signature_key.encode(), message.encode(), hashlib.sha256).digest()
    computed_b64 = base64.b64encode(computed).decode()

    return hmac.compare_digest(computed_b64, received_signature)
