"""
Verification service dispatcher.

Routes verification requests to the appropriate provider by method.
Currently only "bvn" is wired up (via Prembly). Other methods (e.g. NIN)
will raise NotImplementedError until their integrations are built.

IMPORTANT: this module (and everything it calls) is the only place the
raw BVN/NIN should ever exist in memory. Never log, persist, or return
the raw number anywhere outside the outbound request to the provider.
"""

from typing import TypedDict

from opus_escrow.config import get_settings
from opus_escrow.integrations.prembly import verify_bvn


class VerificationResult(TypedDict):
    success: bool
    reference: str  # opaque reference/token to store - NEVER the raw number
    status: str      # e.g. "verified", "failed"


async def call_verification_service(method: str, number: str) -> VerificationResult:
    settings = get_settings()

    if settings.verification_mock_mode:
        # DEV ONLY - fakes a successful verification so onboarding can be
        # tested end-to-end without hitting (and paying for) a real
        # Prembly call every time.
        return VerificationResult(success=True, reference="MOCK-REF-0001", status="verified")

    if method == "bvn":
        result = await verify_bvn(number)
        return VerificationResult(
            success=result["success"],
            reference=result["reference"],
            status="verified" if result["success"] else "failed",
        )

    raise NotImplementedError(f"Verification method '{method}' isn't wired up yet - bvn is the only one live.")