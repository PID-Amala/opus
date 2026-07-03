"""
Initialize (or update) all collections for the database pointed to by
MONGO_URI/MONGO_DB_NAME in .env.

Usage:
    python -m app.db.init_db

Safe to re-run: existing collections get their validator updated via
collMod, and create_index() is idempotent.
"""

import asyncio
import logging

from pymongo.errors import CollectionInvalid

from opus_escrow.config import get_settings
from opus_escrow.db.client import close_client, get_database
from opus_escrow.db.schemas import COLLECTIONS

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("init_db")


async def init_collection(db, name: str, definition: dict) -> None:
    validator = definition.get("validator")
    indexes = definition.get("indexes", [])

    try:
        await db.create_collection(name, validator=validator)
        logger.info("  + created collection: %s", name)
    except CollectionInvalid:
        if validator:
            await db.command("collMod", name, validator=validator)
            logger.info("  = collection exists, validator updated: %s", name)
        else:
            logger.info("  = collection exists: %s", name)

    for index in indexes:
        keys = index["keys"]
        options = index.get("options", {})
        index_name = await db[name].create_index(keys, **options)
        logger.info("    - ensured index: %s", index_name)


async def main() -> None:
    settings = get_settings()
    db = get_database()

    logger.info("Initializing database '%s'...\n", settings.mongo_db_name)

    for name, definition in COLLECTIONS.items():
        await init_collection(db, name, definition)

    logger.info("\nDone. %d collections up to date.", len(COLLECTIONS))
    await close_client()


if __name__ == "__main__":
    asyncio.run(main())
