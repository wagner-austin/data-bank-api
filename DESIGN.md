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
ASGI Server: Hypercorn
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
  - `file_id`: server-generated sha256 hex digest of the uploaded content (lowercase). This serves as both the content address (dedupe-friendly) and the HTTP `ETag`.

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
- Implementation detail: metadata is written as a best‑effort sidecar file alongside the blob (e.g., `/data/files/ab/cd/<file_id>.meta`). HEAD/INFO read from sidecar when available and fall back to recomputing `sha256` from the blob if missing/invalid.

---

## API Design

### Authentication
- Header: `X-API-Key: <service-key>`.
- Per-service keys (turkic-api, model-trainer) with scoped permissions (upload/read/delete).
- Apply simple rate limits per key to prevent abuse.

### Endpoints
- `POST /files` (multipart/form-data)
  - Input: single part `file`.
  - The server ignores the multipart filename for identity. The file is streamed to a temp file, `sha256` is computed while streaming, then the blob is fsync+renamed atomically to the final path derived from the `file_id` (sha256). Metadata is captured (best‑effort sidecar).
  - Returns: `201 { file_id, size, sha256, content_type, created_at }`.
  - Errors: 400 (bad multipart), 401/403 (auth), 413 (too large; code `PAYLOAD_TOO_LARGE`), 415 (unsupported), 507 (insufficient storage), 500.

- `GET /files/{file_id}`
  - Streaming response; supports Range requests.
  - Headers: `Accept-Ranges: bytes`, `Content-Length`, `Content-Type`, `ETag`, optional `Content-Disposition`. `ETag` and `Content-Type` are returned for both full (200) and ranged (206) responses.
  - 206 for valid ranges, 416 for unsatisfiable ranges.
  - Errors: 404, 416, 401/403, 500.

- `HEAD /files/{file_id}`
  - Same headers as GET (no body). Used by clients for presence/size/ETag probe.

- `GET /files/{file_id}/info`
  - JSON metadata: `{ file_id, size, sha256, content_type, created_at }`.

- `DELETE /files/{file_id}`
  - 204 on success. Default: idempotent (returns 204 even if missing).
  - Config: `DELETE_STRICT_404=true` switches to strict 404 on missing.

- `GET /healthz`
  - 200 when process is up.

- `GET /readyz`
  - 200 when storage root exists, is writable, `MIN_FREE_GB` satisfied, and a small write+fsync probe succeeds; otherwise 503.

### HTTP Semantics & Errors
- Range support:
  - Single-range bytes (e.g., `bytes=0-`, `bytes=100-199`), with correct `Content-Range` and `Content-Length`.
  - 416 on invalid ranges with `Content-Range: bytes */<size>`.
- ETag is `sha256`; future: support `If-None-Match` for caching.
- Error payloads are consistent JSON with fields:
  - `code` (string, machine-readable), `message` (string), `request_id` (string).
  - Example: `{ "code": "NOT_FOUND", "message": "file not found", "request_id": "req-123" }`.

---

## Security
- Strict `file_id` validation (hex-only, length bound). No user-controlled path assembly.
- API keys per client; scope enforcement; per-key rate limiting.
- Private network only, plus key checks (defense in depth).

### API Key Scopes
- Keys carry minimal scopes:
  - `files:write` for `POST /files`.
  - `files:read` for `HEAD/GET /files/{id}`, `/info`.
  - `files:delete` for `DELETE /files/{id}`.
  - Reject requests lacking required scope with 403.

---

## Testing Strategy

### Unit
- Atomic write & rename; fsync behaviors (simulate failures).
- Size and free-space guards; correct 507 propagation.
- Range handling: first bytes, tail, mid-range; invalid ranges (416).
- Metadata/HEAD parity and consistent ETag.
- Delete semantics (idempotent vs strict 404 path).
- Auth and rate-limiting.

### Request Correlation
- Accept `X-Request-ID` from callers; generate UUIDv4 if absent. Echo on all responses and include in logs.

### Integration
- Upload → HEAD → download round-trip; large-file streaming via temp-backed generators.
- Failure injection: partial writes, disk full, permission errors.
- Cross-service flows (stubs): turkic-api upload; model-trainer HEAD+GET stream; verify sha256/size.

---

## Observability

### Logging
- Structured JSON fields: `ts`, `level`, `request_id`, `route`, `status`, `elapsed_ms`, `file_id`, `size_bytes`, `range`, `client`.
- No print(); logging centralized and consistent.

### Makefile (suggested)
- `make check`: ruff (fix) → ruff format → mypy strict → guards → tests with branch coverage.
- `make test`: run pytest with `--cov-branch`.
- `make lint`: ruff + mypy, no code changes beyond ruff --fix.

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
  - Example internal URL: `http://data-bank-api.railway.internal` in the same environment.

---

## Integration Points

### turkic-api → data-bank-api (Upload)

**Purpose:** Upload assembled corpus files to data-bank-api after job completion.

**Files to Modify:**

1. **`api/config.py`** - Add data-bank-api credentials:
   ```python
   @dataclass(frozen=True)
   class Settings:
       redis_url: str
       data_dir: str
       environment: str
       data_bank_api_url: str      # NEW
       data_bank_api_key: str      # NEW

       @staticmethod
       def from_env() -> Settings:
           prefix = "TURKIC_"
           # ... existing fields ...
           data_bank_api_url = os.getenv(f"{prefix}DATA_BANK_API_URL", "").strip()
           data_bank_api_key = os.getenv(f"{prefix}DATA_BANK_API_KEY", "").strip()
           return Settings(..., data_bank_api_url=data_bank_api_url, data_bank_api_key=data_bank_api_key)
   ```

2. **`api/jobs.py`** - Upload after job completion (after line 127):
   ```python
   # Job completed successfully - upload to data-bank-api
   file_id: str | None = None
   if settings.data_bank_api_url and settings.data_bank_api_key:
       try:
           import httpx
           with out_path.open("rb") as f:
               resp = httpx.post(
                   f"{settings.data_bank_api_url}/files",
                   headers={"X-API-Key": settings.data_bank_api_key},
                   files={"file": (f"{job_id}.txt", f, "text/plain; charset=utf-8")},
                   timeout=600.0,  # 10min for large files
               )
               resp.raise_for_status()
               data = resp.json()
               file_id = data["file_id"]
               logger.info("Uploaded to data-bank-api", extra={"job_id": job_id, "file_id": file_id})
       except Exception as exc:
           logger.error("Failed to upload to data-bank-api", extra={"job_id": job_id, "error": str(exc)})
           # Don't fail the job - file is still available locally

   # Store file_id in Redis job hash
   redis.hset(
       f"job:{job_id}",
       mapping={
           "status": "completed",
           "updated_at": datetime.utcnow().isoformat(),
           "progress": "100",
           "message": "done",
           "file_id": file_id or "",  # NEW
       },
   )
   ```

3. **`api/models.py`** - Add `file_id` to response schema:
   ```python
   class JobStatus(BaseModel):
       job_id: str
       status: Literal["queued", "processing", "completed", "failed"]
       progress: int
       message: str | None = None
       result_url: str | None = None
       file_id: str | None = None  # NEW: data-bank-api file ID (when completed)
       created_at: datetime
       updated_at: datetime
       error: str | None = None
   ```

4. **`pyproject.toml`** - Add httpx dependency:
   ```toml
   [tool.poetry.dependencies]
   httpx = "^0.28.0"  # NEW: for data-bank-api uploads
   ```

**Environment Variables (Railway):**
```bash
TURKIC_DATA_BANK_API_URL="http://data-bank-api.railway.internal"
TURKIC_DATA_BANK_API_KEY="dbapi_turkic_74469347850f4a9a2f431f358692899d392e21a37b75a6f8921bfdbdd37f289c"
```

**User Flow:**
1. `POST /api/v1/jobs` → `{"job_id": "abc123"}`
2. Poll `GET /api/v1/jobs/abc123` until `status == "completed"`
3. Response includes `file_id: "xyz789"` (data-bank-api file ID)
4. Pass `file_id` to model-trainer

---

### model-trainer ← data-bank-api (Download)

**Purpose:** Download corpus files from data-bank-api before training.

**Files to Modify:**

1. **`server/model_trainer/core/config/settings.py`** - Add data-bank-api config:
   ```python
   class AppConfig(BaseSettings):
       data_root: str = "/data"
       artifacts_root: str = "/data/artifacts"
       runs_root: str = "/data/runs"
       logs_root: str = "/data/logs"
       data_bank_api_url: str = ""      # NEW
       data_bank_api_key: str = ""      # NEW
       # ... existing fields ...
   ```

2. **`server/model_trainer/api/schemas/runs.py`** - Add `corpus_file_id` field:
   ```python
   class TrainRequest(BaseModel):
       model_family: Annotated[Literal["gpt2", "llama", "qwen"], Field(default="gpt2")]
       model_size: Annotated[str, Field(default="small")]
       # ... existing fields ...
       corpus_path: Annotated[str | None, Field(default=None, description="Filesystem path to corpus")]
       corpus_file_id: Annotated[str | None, Field(default=None, description="data-bank-api file ID")]
       tokenizer_id: Annotated[str, Field(description="Tokenizer artifact ID to use")]

       @field_validator("corpus_path", "corpus_file_id")
       @classmethod
       def validate_corpus_source(cls, v: str | None, info: ValidationInfo) -> str | None:
           # Ensure exactly one is provided
           values = info.data
           corpus_path = values.get("corpus_path")
           corpus_file_id = values.get("corpus_file_id")
           if not corpus_path and not corpus_file_id:
               raise ValueError("Either corpus_path or corpus_file_id must be provided")
           if corpus_path and corpus_file_id:
               raise ValueError("Only one of corpus_path or corpus_file_id can be provided")
           return v
   ```

3. **`server/model_trainer/core/services/data/corpus_fetcher.py`** - NEW service:
   ```python
   from pathlib import Path
   import httpx
   import hashlib

   class CorpusFetcher:
       def __init__(self, api_url: str, api_key: str, cache_dir: Path) -> None:
           self._api_url = api_url
           self._api_key = api_key
           self._cache_dir = cache_dir
           self._cache_dir.mkdir(parents=True, exist_ok=True)

       def fetch(self, file_id: str) -> Path:
           """Download corpus from data-bank-api, cache locally, return path."""
           cache_path = self._cache_dir / f"{file_id}.txt"

           # Return cached if exists and valid
           if cache_path.exists():
               return cache_path

           # Download from data-bank-api
           headers = {"X-API-Key": self._api_key}
           url = f"{self._api_url}/files/{file_id}"

           # HEAD to get size and ETag
           head_resp = httpx.head(url, headers=headers, timeout=30.0)
           head_resp.raise_for_status()
           expected_size = int(head_resp.headers["content-length"])
           etag = head_resp.headers.get("etag", "")

           # Stream download with resume support
           temp_path = cache_path.with_suffix(".tmp")
           start_byte = temp_path.stat().st_size if temp_path.exists() else 0

           if start_byte > 0:
               headers["Range"] = f"bytes={start_byte}-"

           with httpx.stream("GET", url, headers=headers, timeout=600.0) as resp:
               resp.raise_for_status()
               mode = "ab" if start_byte > 0 else "wb"
               with temp_path.open(mode) as f:
                   for chunk in resp.iter_bytes(chunk_size=1024 * 1024):  # 1MB chunks
                       f.write(chunk)

           # Verify size
           if temp_path.stat().st_size != expected_size:
               raise RuntimeError(f"Size mismatch: expected {expected_size}, got {temp_path.stat().st_size}")

           # Atomic rename
           temp_path.rename(cache_path)
           return cache_path
   ```

4. **`server/model_trainer/orchestrators/training_orchestrator.py`** - Resolve corpus before enqueue:
   ```python
   def train(self, request: TrainRequest, req: Request) -> TrainResponse:
       # ... existing run_id generation ...

       # Resolve corpus path
       if request.corpus_file_id:
           # Download from data-bank-api
           fetcher = CorpusFetcher(
               api_url=self._settings.app.data_bank_api_url,
               api_key=self._settings.app.data_bank_api_key,
               cache_dir=Path(self._settings.app.data_root) / "corpus_cache",
           )
           corpus_path = str(fetcher.fetch(request.corpus_file_id))
       else:
           corpus_path = request.corpus_path  # Use provided filesystem path

       # Build request payload with resolved path
       request_payload: TrainRequestPayload = {
           # ... existing fields ...
           "corpus_path": corpus_path,
       }
       # ... rest of function ...
   ```

5. **`pyproject.toml`** - Ensure httpx is in dependencies (already present).

**Environment Variables (Railway):**

For **model-trainer-api** service:
```bash
DATA_BANK_API_URL="http://data-bank-api.railway.internal"
DATA_BANK_API_KEY="dbapi_trainer_f6839ba5dad97cf67f12daf160458e9ebdb255a5df052466ca719d97e238e6e1"
```

For **model-trainer-worker** service:
```bash
DATA_BANK_API_URL="http://data-bank-api.railway.internal"
DATA_BANK_API_KEY="dbapi_trainer_f6839ba5dad97cf67f12daf160458e9ebdb255a5df052466ca719d97e238e6e1"
```

**User Flow:**
1. Get `file_id` from turkic-api (or upload directly to data-bank-api)
2. `POST /runs/train` with `corpus_file_id: "xyz789"`
3. model-trainer downloads corpus, caches at `/data/corpus_cache/xyz789.txt`
4. Training proceeds with local cached file
5. Cached file persists across jobs (no re-download needed)

---

### Error Handling

**turkic-api Upload Failure:**
- Log error but don't fail the job
- Job status remains "completed"
- `file_id` will be empty string or null
- User can still download via `/api/v1/jobs/{job_id}/result` endpoint

**model-trainer Download Failure:**
- Return 400 Bad Request with error details
- Log failure with run_id and file_id
- Do not enqueue training job
- Common errors:
  - 401/403: Invalid API key
  - 404: File not found in data-bank-api
  - 507: data-bank-api out of space
  - Network timeout: Retry with exponential backoff

**Cache Management:**
- model-trainer cache stored at `/data/corpus_cache/{file_id}.txt`
- No automatic cleanup (manual pruning or TTL-based cleanup can be added later)
- Resume support: partial downloads continue from last byte

---

## Reliability for GB-scale Files
- Always stream (1–8 MB chunks); never buffer entire file.
- Retries with exponential backoff on transient errors; verify final byte count; optional hash verification.
- Enforce `MIN_FREE_GB`; return 507 early if not satisfied.
- TTL cleanup and optional LRU reclamation when nearing space threshold.

### Dynamic Storage Considerations
- No hardcoded `MAX_FILE_BYTES` default. Enforce runtime free-space guard via `MIN_FREE_GB` or `MIN_FREE_PERCENT`.
- Prefer `Content-Length` on upload; if absent, stream with a reserved margin and abort when projected free space falls below guard before atomic rename.

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

