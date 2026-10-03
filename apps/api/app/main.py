from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .config import settings
from .db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="TryOnU VTO", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_methods=["*"], allow_headers=["*"],
)
app.include_router(router)

# Mount only the media folders; never the storage root (it holds the SQLite DB).
for _kind in ("persons", "products", "results"):
    (settings.storage_dir / _kind).mkdir(parents=True, exist_ok=True)
    app.mount(f"/files/{_kind}", StaticFiles(directory=settings.storage_dir / _kind), name=f"files-{_kind}")
