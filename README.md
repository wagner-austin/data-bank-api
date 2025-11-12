# data-bank-api

Strictly typed file storage API for internal service-to-service exchange on Railway. Provides streaming uploads/downloads with Range/HEAD, atomic writes, metadata (sha256, size), disk-space guards, TTL/quotas, JSON logs, and private-network auth for turkic-api and model-trainer.

## Docker

Build a production image (multi-stage, non-root, healthcheck):

- docker build -t data-bank-api:latest .

Run locally with a bind mount for data:

- docker run --rm -p 8000:8000 \
  -e DATA_ROOT=/data/files \
  -e API_UPLOAD_KEYS=turkic-u1 \
  -e API_READ_KEYS=trainer-r1 \
  -e API_DELETE_KEYS=trainer-r1 \
  -v ${PWD}/data:/data/files \
  data-bank-api:latest

Test:

- curl http://localhost:8000/healthz
- curl http://localhost:8000/readyz

Notes:
- The image exposes port 8000 and runs `uvicorn data_bank_api.app:create_app --factory`.
- Default `DATA_ROOT` is `/data/files` inside the container. Override via env if needed.

## Railway Deployment

Recommended setup (one service named `data-bank-api`):

1. Create a Railway project and add a new service from this repo (or use the Dockerfile).
2. Configure a persistent volume and mount it at `/data/files`.
3. Set environment variables:
   - `DATA_ROOT=/data/files`
   - `MIN_FREE_GB=1` (or as needed)
   - `API_UPLOAD_KEYS=turkic-u1` (comma-separated values)
   - `API_READ_KEYS=trainer-r1` (inherits from upload if omitted)
   - `API_DELETE_KEYS=trainer-r1` (inherits from upload if omitted)
   - `DELETE_STRICT_404=false` (set `true` if you want 404 on missing delete)
4. Start command (if not using Dockerfile CMD):
   - `uvicorn data_bank_api.app:create_app --factory --host 0.0.0.0 --port 8000`
5. Health checks (Railway UI):
   - Health: `/healthz`
   - Ready: `/readyz`
6. Networking:
   - Keep `data-bank-api` on the private network.
   - Other internal services (e.g., `turkic-api`, `model-trainer`) use the private URL, e.g. `http://data-bank-api.railway.internal:8000`.
7. Configure clients:
   - Set `DATA_BANK_URL` in turkic-api/model-trainer to `http://data-bank-api.railway.internal:8000`.
   - Set `DATA_BANK_KEY` to a valid key (upload for producer; read/delete for consumer) and pass it to the client.

## Python Client

This repo includes a typed client suitable for reuse in `turkic-api` and `model-trainer`.

- from data_bank_api.client import DataBankClient
- c = DataBankClient(base_url=os.environ["DATA_BANK_URL"], api_key=os.environ["DATA_BANK_KEY"])
- Upload: `c.upload(file_id, open(path, "rb"), content_type)`
- Download: `c.download_to_path(file_id, Path(out), resume=True, verify_etag=True)`
- Probe: `c.head(file_id)`; `c.info(file_id)`
- Delete: `c.delete(file_id)`

## Development

- make check  # ruff, format, mypy, tests (with branch coverage)
- make test   # run tests only
