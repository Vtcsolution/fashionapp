class FashnResult:
    def __init__(self, fashn_job_id: str, image_bytes: bytes):
        self.fashn_job_id = fashn_job_id
        self.image_bytes = image_bytes  # exactly as returned, never post-processed


class FashnError(Exception):
    """A FASHN failure. Always terminal: nothing in this codebase retries a FASHN request."""

    def __init__(self, kind: str, message: str = "", http_status: int | None = None):
        self.kind, self.http_status = kind, http_status
        super().__init__(f"{kind}{f' (HTTP {http_status})' if http_status else ''}: {message}".strip())
