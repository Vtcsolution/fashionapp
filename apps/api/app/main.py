import json
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .api.runs import router as runs_router
from .config import settings
from .db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="TryOnU VTO", lifespan=lifespan)
def _origins(raw: str) -> list[str]:
    """CORS_ORIGINS may be a JSON list (as in .env) or comma-separated."""
    try:
        v = json.loads(raw)
        return [str(o) for o in v] if isinstance(v, list) else [str(v)]
    except ValueError:
        return [o.strip() for o in raw.split(",") if o.strip()]


app.add_middleware(
    CORSMiddleware, allow_origins=_origins(settings.cors_origins),
    allow_methods=["*"], allow_headers=["*"],
)
app.include_router(router)
app.include_router(runs_router)

# Mount only the media folders; never the storage root (it holds the SQLite DB).
for _kind in ("persons", "products", "results"):
    (settings.storage_dir / _kind).mkdir(parents=True, exist_ok=True)
    app.mount(f"/files/{_kind}", StaticFiles(directory=settings.storage_dir / _kind), name=f"files-{_kind}")
