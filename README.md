# Bulk Certificate Generator

Backend API that takes a single request containing many recipients and produces
one PDF certificate per valid recipient from a fixed template. Generation runs in
the background with live progress; certificates can be downloaded individually or
as one ZIP.

Stack: FastAPI, SQLAlchemy 2, Pydantic v2, ReportLab, Celery + Redis (task queue),
Alembic (migrations), PostgreSQL (SQLite for local runs and tests), pytest, Docker.

## Run with Docker

```bash
docker compose up --build -d
```

- API: http://localhost:8000
- Swagger UI: http://localhost:8000/docs

The stack runs four services — `web` (API), `worker` (Celery), `db` (PostgreSQL) and
`redis` (queue). Database migrations run automatically on startup.

```bash
docker compose down       # stop (keeps DB and stored PDFs)
docker compose down -v    # stop and delete DB + stored PDFs
```

## Run locally

```bash
python -m venv .venv && . .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload
```

With no configuration the app uses SQLite (`./certificates.db`) and stores PDFs in
`./storage/certificates`. Set `DATABASE_URL` to use PostgreSQL.

Jobs are dispatched to a Celery queue. To run locally without Redis, set
`CELERY_TASK_ALWAYS_EAGER=true` and tasks execute inline. To run the real queue, start
Redis, drop that variable, and also run a worker:

```bash
celery -A app.celery_app.celery_app worker --loglevel=info
```

## Tests

```bash
pytest -v            # local (tasks run inline via CELERY_TASK_ALWAYS_EAGER)
make test            # in Docker, in the real runtime image
```

## Submit a job

```bash
curl -i -X POST http://localhost:8000/api/v1/certificate-jobs \
  -H "Content-Type: application/json" \
  -d '{
    "course_name": "Advanced Python Programming",
    "issued_by": "Arena Academy",
    "issued_on": "2026-10-11",
    "recipients": [
      {"full_name": "Priya Sharma", "email": "priya@example.com"},
      {"full_name": "Aisha Khan"}
    ]
  }'
```

Responds `202 Accepted` with a `Location` header pointing at the status endpoint.
A ready-made request is in `examples/request.json`.

If `API_KEY` is set, every endpoint requires an `X-API-Key` header:

```bash
curl -i -X POST http://localhost:8000/api/v1/certificate-jobs \
  -H "Content-Type: application/json" -H "X-API-Key: $API_KEY" \
  --data @examples/request.json
```

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| POST | /api/v1/certificate-jobs | Submit a bulk job (202 + Location) |
| GET | /api/v1/certificate-jobs/{job_id} | Status, counters, progress_percent |
| GET | /api/v1/certificate-jobs/{job_id}/certificates | Per-recipient results (?status=) |
| GET | /api/v1/certificates/{certificate_id}/download | One PDF (409 if not generated) |
| GET | /api/v1/certificate-jobs/{job_id}/download | All generated PDFs as one ZIP |
| GET | /health | Liveness probe |

## Configuration

All settings come from environment variables (see `.env.example`):

| Variable | Default | Meaning |
| --- | --- | --- |
| DATABASE_URL | sqlite:///./certificates.db | SQLAlchemy database URL |
| STORAGE_DIR | ./storage/certificates | Where PDFs are written |
| MAX_RECIPIENTS_PER_JOB | 1000 | Upper bound per request |
| MAX_WORKERS | 4 | Threads used to render PDFs |
| CORS_ORIGINS | * | Comma-separated allowed origins |
| WEB_CONCURRENCY | 2 | Uvicorn worker processes |
| PORT | 8000 | HTTP port |
| API_KEY | (empty) | Required `X-API-Key`; empty disables auth |
| ENABLE_DOCS | true | Expose `/docs`, `/redoc`, `/openapi.json` |
| CELERY_BROKER_URL | redis://localhost:6379/0 | Queue broker |
| CELERY_RESULT_BACKEND | (empty) | Optional Celery result backend |
| CELERY_TASK_ALWAYS_EAGER | false | Run tasks inline (no broker; for dev/tests) |

## Migrations

Schema is managed with Alembic. The Docker image applies migrations automatically on
startup; locally run:

```bash
alembic upgrade head                       # apply
alembic revision --autogenerate -m "msg"   # create a new migration
```

## Notes

- Jobs are executed by a separate Celery worker process, so an API restart no longer
  strands an in-flight job. A worker only adopts a job still in `queued`, so a duplicate
  delivery is a no-op.
- Errors stored against a certificate are a generic message (`Generation failed.`); the
  full traceback is written to the worker log, not to the API response.
- The ZIP download is streamed from a spooled temporary file, so large batches are not
  held in memory.
