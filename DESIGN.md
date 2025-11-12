# data-bank-api — Design Document (Revised)

## Project Vision

Production-grade file storage API serving as the data integration layer between turkic-api (producer) and model-trainer (consumer). Emphasis on: zero technical debt, strict type safety, reliable GB-scale transfers, Railway-first deployment, and clean service boundaries.

---

## Quality Standards (Non-Negotiable)

### Type Safety
- mypy --strict enabled.
- Zero Any (explicit or implicit), zero casts, zero type: ignore.
- All public functions precisely typed with concrete return types.

### Test Coverage
- 100% statements and branches (pytest-cov with --cov-branch).
- Unit tests for storage, HTTP handlers, error mapping, and edge cases.
- Integration tests for upload/download/HEAD/Range and failure injection.

### Code Quality
- DRY, modular, single responsibility, consistent style.
- Structured JSON logs; no print(); request correlation.

### Process
- make check runs: ruff (fix), ruff format, mypy strict, guard checks, and tests; must pass before commit.
- Guards ban Any/casts/ignores and drift markers (TODO/FIXME/HACK).

---

## Technology & Infrastructure

### Backend (Python 3.11+)
```yaml
Framework: FastAPI (async/await)
ASGI Server: Uvicorn
Deps: Poetry
Typing: mypy (strict)
Linting: Ruff
Testing: pytest + pytest-cov + pytest-asyncio
```

### Infrastructure
```yaml
Hosting: Railway
Volume: Dedicated Persistent Volume for data-bank-api
Network: Railway private network (*.railway.internal)
CI/CD: Railway auto-deploy from git
```

---

## Architecture Overview

- Single service owning a filesystem-backed store at /data/files (configurable root).
- HTTP API provides upload (multipart), download (with Range), HEAD metadata, info, delete, and health/ready probes.
- Storage service enforces atomic writes, size limits, disk-space guards, metadata capture, and TTL/quotas.
- Observability: structured logs + basic metrics (bytes in/out, latencies, counts).

---

## Storage Model

### Layout
- Hierarchical paths to avoid hot directories:
  - `/data/files/ab/cd/abcdef0123456789.bin`
  - `file_id`: hex digest (e.g., sha256) or server-generated UUIDv4 rendered as lowercase hex; length-limited, hex-only.

### Atomic Writes
- Stream to a temp file (e.g., `.tmp`), fsync, and atomic rename to final path.
- Fsync parent directory entry after rename when applicable.

### Safety & Governance
- Reject path traversal; never interpolate user-supplied segments into OS paths.
- Enforce `MAX_FILE_BYTES` and validate Content-Length when provided.
- Pre-check free space (e.g., `MIN_FREE_GB` threshold) and return 507 on insufficient storage.
- TTL cleanup job to reclaim old files; optional per-service quotas (bytes and/or file count).

### Metadata
- Persist `size_bytes`, `content_type`, `sha256`, and `created_at`. Use `sha256` as `ETag` for HTTP caching semantics.

---

## API Design

### Authentication
- Header: `X-API-Key: <service-key>`.
- Per-service keys (turkic-api, model-trainer) with scoped permissions (upload/read/delete).
- Apply simple rate limits per key to prevent abuse.

### Endpoints
- `POST /files` (multipart/form-data)
  - Input: single part `file`.
  - Stream to temp, compute sha256, fsync+rename. Capture metadata.
  - Returns: `201 { file_id, size, sha256, content_type, created_at }`.
  - Errors: 400 (bad multipart), 401/403 (auth), 413 (too large), 415 (unsupported), 507 (insufficient storage), 500.

- `GET /files/{file_id}`
  - Streaming response; supports Range requests.
  - Headers: `Accept-Ranges: bytes`, `Content-Length`, `Content-Type`, `ETag`, optional `Content-Disposition`.
  - 206 for valid ranges, 416 for unsatisfiable ranges.
  - Errors: 404, 416, 401/403, 500.

- `HEAD /files/{file_id}`
  - Same headers as GET (no body). Used by clients for presence/size/ETag probe.

- `GET /files/{file_id}/info`
  - JSON metadata: `{ file_id, size, sha256, content_type, created_at }`.

- `DELETE /files/{file_id}`
  - 204 on success. Idempotency: either 204 for missing or strict 404 (configurable).

- `GET /healthz`
  - 200 when process is up.

- `GET /readyz`
  - 200 when storage root exists, is writable, `MIN_FREE_GB` satisfied, and a small write+fsync probe succeeds; otherwise 503.

### HTTP Semantics & Errors
- Range support:
  - Single-range bytes (e.g., `bytes=0-`, `bytes=100-199`), with correct `Content-Range` and `Content-Length`.
  - 416 on invalid ranges with `Content-Range: bytes */<size>`.
- ETag is `sha256`; future: support `If-None-Match` for caching.
- Consistent JSON error payloads with `code`, `message`, and `request_id`.

---

## Security
- Strict `file_id` validation (hex-only, length bound). No user-controlled path assembly.
- API keys per client; scope enforcement; per-key rate limiting.
- Private network only, plus key checks (defense in depth).

---

## Testing Strategy

### Unit
- Atomic write & rename; fsync behaviors (simulate failures).
- Size and free-space guards; correct 507 propagation.
- Range handling: first bytes, tail, mid-range; invalid ranges (416).
- Metadata/HEAD parity and consistent ETag.
- Delete semantics (idempotent vs strict 404 path).
- Auth and rate-limiting.

### Integration
- Upload → HEAD → download round-trip; large-file streaming via temp-backed generators.
- Failure injection: partial writes, disk full, permission errors.
- Cross-service flows (stubs): turkic-api upload; model-trainer HEAD+GET stream; verify sha256/size.

---

## Observability

### Logging
- Structured JSON fields: `ts`, `level`, `request_id`, `route`, `status`, `elapsed_ms`, `file_id`, `size_bytes`, `range`, `client`.
- No print(); logging centralized and consistent.

### Metrics (initial)
- Counters: uploads/downloads/deletes by status; bytes_in/out.
- Gauges: disk_free_gb, file_count.
- Histograms: upload/download duration buckets.

---

## Deployment (Railway)
- Single service with a dedicated volume mounted at `/data/files`.
- Healthcheck Path: `/healthz` for deploy gating; `/readyz` for storage readiness monitoring.
- Variables: `DATA_ROOT`, `MAX_FILE_BYTES`, `MIN_FREE_GB`, service API keys, rate limit config.
- Private-network access from turkic-api and model-trainer via `http://data-bank-api.<env>.railway.internal`.

---

## Integration Points

### turkic-api → data-bank-api (Upload)
- After corpus assembly completes:
  - `POST /files` with file payload and `X-API-Key`.
  - Parse `file_id`; persist in turkic-api job record (Redis) for downstream use.

### model-trainer ← data-bank-api (Download)
- Accept `file_id` in training request (preferred over raw URLs).
- Worker flow:
  - `HEAD /files/{file_id}`: check presence and `Content-Length`/`ETag`.
  - `GET /files/{file_id}` streaming to temp file under `/data/corpus`, periodic progress events (MB), atomic rename, verify bytes (or ETag).
  - Train from local path; schedule TTL cleanup of cached file when appropriate.

---

## Reliability for GB-scale Files
- Always stream (1–8 MB chunks); never buffer entire file.
- Retries with exponential backoff on transient errors; verify final byte count; optional hash verification.
- Enforce `MIN_FREE_GB`; return 507 early if not satisfied.
- TTL cleanup and optional LRU reclamation when nearing space threshold.

---

## Future: Object Storage Backend (Migration Path)
- Introduce S3/R2 backend with pre-signed URLs:
  - Upload: client PUTs to pre-signed URL; POST finalize metadata.
  - Download: client GETs pre-signed URL; supports large/resumable transfers.
- Keep filesystem backend as default; storage adapter interface remains stable to prevent drift.

---

## Design & Implementation Principles
- Factory + DI for testability; no global state.
- Single storage adapter centralizes path rules and IO; handlers remain thin.
- Shared error mapping and response builders to avoid duplication.
- Guards enforce no Any/casts/ignores; repository stays strictly typed.

---

## Open Questions (to confirm)
- Max file size defaults (e.g., 5 GiB) and error behavior when exceeded.
- Minimum free space threshold (e.g., 2 GiB) for `/readyz` and uploads.
- Quotas: per-service byte caps and/or file count; enforcement strategy.
- Delete: idempotent 204 vs strict 404 on missing.
- Required content types vs accepting octet-stream by default.

