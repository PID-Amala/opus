from typing import Optional

import certifi
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from opus_escrow.config import get_settings

_client: Optional[AsyncMongoClient] = None


def get_client() -> AsyncMongoClient:
    """Return a lazily-created, process-wide Mongo client."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = AsyncMongoClient(settings.mongo_uri, tlsCAFile=certifi.where())
    return _client


def get_database() -> AsyncDatabase:
    settings = get_settings()
    return get_client()[settings.mongo_db_name]


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None