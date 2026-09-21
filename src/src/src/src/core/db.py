from prisma import Prisma
import logging

logger = logging.getLogger("mtg_backend.db")

db = Prisma(auto_register=True)

async def connect_db():
    if not db.is_connected():
        logger.info("Connecting to database with Prisma client...")
        await db.connect()
        logger.info("Prisma connected successfully.")

async def disconnect_db():
    if db.is_connected():
        logger.info("Disconnecting Prisma client...")
        await db.disconnect()
        logger.info("Prisma disconnected.")
