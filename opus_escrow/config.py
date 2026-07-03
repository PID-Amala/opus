from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    mongo_uri: str
    mongo_db_name: str = "opus_escrow"
    environment: str = "development"

    # Nomba - only client_id/secret required for now. account_id and
    # signature_key are needed once we wire up virtual accounts/webhooks.
    nomba_base_url: str = "https://api.nomba.com"
    nomba_client_id: Optional[str] = None
    nomba_client_secret: Optional[str] = None
    nomba_account_id: Optional[str] = None
    nomba_signature_key: Optional[str] = None
    nomba_webhook_signature_header: str = "signature"  # unconfirmed - see integrations/nomba.py
    nomba_webhook_timestamp_header: str = "timestamp"  # unconfirmed - see integrations/nomba.py
    prembly_secret_key: Optional[str] = None  # test or live secret key from Prembly dashboard
    gemini_api_key: Optional[str] = None
    whatsapp_verify_token: Optional[str] =None
    telegram_bot_token: Optional[str] = None

    # DEV ONLY: bypasses the real verification API with a fake success
    # result, so onboarding can be tested before the verification API
    # is wired up. Must be False outside local testing.
    verification_mock_mode: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()