# TryOnU VTO (fresh prototype)

Real person photo + real retailer product -> FASHN Try-On Max -> verified result.

- `apps/web` - Next.js + TypeScript + Tailwind
- `apps/api` - FastAPI + SQLite
- `storage/` - local files (git-ignored)

Secrets live in the root `.env` (never committed). FASHN live calls are disabled by default; all development uses mocks.
