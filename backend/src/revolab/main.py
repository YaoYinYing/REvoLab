from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from revolab import __version__
from revolab.api import close_model_backend, install_exception_handlers, router
from revolab.bootstrap import build_driver_context, install_drivers
from revolab.config import get_settings
from revolab.drivers import default_registry

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Explicit, deterministic startup: install configured drivers, then start
    # them. A broken configured driver fails startup loudly (no silent fallback).
    install_drivers(default_registry, build_driver_context(settings), settings)
    default_registry.start_all(build_driver_context(settings))
    try:
        yield
    finally:
        default_registry.stop_all()
        # Release the cached model transport (httpx.Client) at shutdown.
        close_model_backend()


app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
app.include_router(router)
install_exception_handlers(app)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
