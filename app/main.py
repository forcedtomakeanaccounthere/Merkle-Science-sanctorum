"""Application factory for the Sanctum Sanctorum Bookstore API."""
from contextlib import asynccontextmanager
from pathlib import Path
from time import sleep

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import text
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (registers tables on Base.metadata)
from app.clock import get_now
from app.db import Base, SessionLocal, engine, get_db
from app.routers import books, loans, members, orders, reports
from app.schemas import HealthOut
from app.seed import seed_if_empty

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
DATABASE_STARTUP_ATTEMPTS = 4


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Create tables on the default engine and load demo data into an empty database."""
    for attempt in range(DATABASE_STARTUP_ATTEMPTS):
        try:
            Base.metadata.create_all(engine)
            with SessionLocal() as db:
                seed_if_empty(db, get_now())
            break
        except SQLAlchemyError:
            engine.dispose()
            if attempt == DATABASE_STARTUP_ATTEMPTS - 1:
                raise
            sleep(2**attempt)
    yield


async def not_implemented_handler(_: Request, exc: NotImplementedError) -> JSONResponse:
    return JSONResponse(status_code=501, content={"detail": f"Not implemented: {exc}"})


def create_app(init_db: bool = True) -> FastAPI:
    application = FastAPI(
        title="Sanctum Sanctorum Bookstore",
        lifespan=lifespan if init_db else None,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.add_exception_handler(NotImplementedError, not_implemented_handler)

    @application.get("/health", response_model=HealthOut, tags=["health"])
    def health():
        return {"status": "ok"}

    @application.get("/health/db", response_model=HealthOut, tags=["health"])
    def database_health(db: Session = Depends(get_db)):
        try:
            db.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            raise HTTPException(status_code=503, detail="Database unavailable") from exc
        return {"status": "ok"}

    application.include_router(books.router)
    application.include_router(members.router)
    application.include_router(orders.router)
    application.include_router(loans.router)
    application.include_router(reports.router)

    # Mounted last so API routes take precedence over static files.
    if FRONTEND_DIR.is_dir():
        application.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

    return application


app = create_app()
