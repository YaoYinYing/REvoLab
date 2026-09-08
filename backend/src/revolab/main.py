from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from revolab import __version__
from revolab.api import install_exception_handlers, router
from revolab.config import get_settings

settings = get_settings()
app = FastAPI(title=settings.app_name, version=__version__)
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
