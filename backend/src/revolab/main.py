from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from revolab import __version__
from revolab.api import router
from revolab.config import get_settings

settings = get_settings()
app = FastAPI(title=settings.app_name, version=__version__)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
