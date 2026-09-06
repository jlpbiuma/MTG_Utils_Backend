import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.core.config import settings
from src.core.db import connect_db, disconnect_db
from src.routers import decks, collection, scryfall, edhrec, pricing, import_cards, worker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mtg_backend")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Initializing MTG Backend Application...")
    try:
        await connect_db()
    except Exception as e:
        logger.warning(f"Database connection warning at startup: {e}")
    yield
    # Shutdown
    logger.info("Shutting down MTG Backend Application...")
    try:
        await disconnect_db()
    except Exception as e:
        logger.warning(f"Error disconnecting database: {e}")

app = FastAPI(
    title="MTG Utils Backend API",
    version="2.0.0",
    description="FastAPI Backend for MTG Utils (Decks, Collection, Scryfall, EDHREC, Pricing)",
    lifespan=lifespan,
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|192\.168\.0\.\d+)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


from src.routers import decks, collection, scryfall, edhrec, pricing, import_cards, worker, auth

# Include API Routers
app.include_router(auth.router, prefix="/api")
app.include_router(decks.router, prefix="/api")
app.include_router(collection.router, prefix="/api")
app.include_router(scryfall.router, prefix="/api")
app.include_router(edhrec.router, prefix="/api")
app.include_router(pricing.router, prefix="/api")
app.include_router(import_cards.router, prefix="/api")
app.include_router(worker.router, prefix="/api")

@app.get("/api/health")
async def health_check():
    return {"status": "ok", "service": "mtg-backend", "version": "2.0.0"}

@app.get("/")
async def root():
    return {"message": "MTG Utils API is running. Visit /docs for Swagger UI."}
