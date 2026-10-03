import os
import tempfile

# Must run before the app is imported: isolate storage/DB.
_tmp = tempfile.mkdtemp(prefix="vto-test-")
os.environ["STORAGE_DIR"] = _tmp
os.environ["VTO_DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"

import io  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from PIL import Image  # noqa: E402


def make_png(w=800, h=1000, color=(40, 90, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def png():
    return make_png


@pytest.fixture(autouse=True)
def safety(monkeypatch):
    """Every test: live FASHN off, and any REAL network request to fashn.ai is impossible."""
    monkeypatch.delenv("FASHN_LIVE_ENABLED", raising=False)
    monkeypatch.delenv("FASHN_LIVE_AUTHORIZATION", raising=False)

    real = httpx.AsyncHTTPTransport.handle_async_request

    async def guarded(self, request):
        if request.url.host.endswith("fashn.ai"):
            raise AssertionError("TEST TRIED TO CALL THE REAL FASHN API")
        return await real(self, request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", guarded)


@pytest.fixture(autouse=True)
def fresh_db():
    from app.db import Base, engine, init_db

    Base.metadata.drop_all(engine)
    init_db()
    yield
