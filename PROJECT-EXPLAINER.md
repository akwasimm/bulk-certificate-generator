# Bulk Certificate Generator — Project Explainer

> A short, readable guide to what this project is, how it works, and why it was built
> the way it was. Written to be understood by a non-technical reader **and** to stand up
> to a technical one.

---

## How to read this document

| If you are… | Start here |
| --- | --- |
| Non-technical (manager, examiner, customer) | Sections **1–5**, then **12** (Running it). Skim the tables. |
| Technical reviewer | Sections **4–11** and **14** (Design decisions & trade-offs). |
| Everyone | The TL;DR below. |

---

## TL;DR

**Plain English.** Training academies, schools and companies often need to hand out
hundreds of completion certificates at once. Making those by hand is slow and
error-prone. This project turns that chore into a single automated request: one
"job" goes in, and one polished PDF certificate comes out for every valid person on
the list. You can then download each certificate on its own or grab the whole batch
packed into a single ZIP file.

**Technical summary.** A stateless HTTP API built with **FastAPI**, persisting state
in **PostgreSQL** through **SQLAlchemy 2**, validating all input with **Pydantic v2**,
and rendering each certificate with **ReportLab**. Jobs are handed to a **Celery worker**
over a **Redis** queue, so generation survives an API restart; within a worker, a single
shared, bounded thread pool renders certificates concurrently with per-item commits for
live progress. The schema is versioned with **Alembic**, downloads are streamed (single
PDF or ZIP), and an optional **API key** guards every endpoint. Ships as a hardened
Docker image running as a non-root user.

---

## 1. What problem does it solve?

Imagine an academy finishing a course with 200 students. Someone has to open a
template, type each name, export a PDF, and repeat. It is repetitive, slow, and one
typo can go out to a paying customer.

This project replaces that process with a single API call:

1. You send one request containing the course details **and the whole list of people**.
2. The system checks each person and quietly flags the ones with bad data.
3. It generates a certificate per valid person, in the background, while you do other work.
4. You check progress at any time, then download the results.

The key idea: **one request in, a whole stack of certificates out.**

---

## 2. The big-picture analogy

Think of it like a **restaurant order**:

- The **API** is the waiter: it takes your order, hands it to the kitchen, and gives
  you a receipt (a "ticket number").
- The **message queue** (Redis) is the order rail: the waiter clips your ticket to it and
  goes back to serving. Even if the waiter's shift ends, the ticket is still on the rail.
- The **background worker** (a separate Celery process) is the kitchen: it pulls tickets
  off the rail and cooks each dish (certificate) independently. If one dish burns, the
  other dishes still arrive.
- The **database** is the order book: it remembers every order, every dish, and its
  status, so anyone can ask "how is order #42 doing?" at any moment.
- The **ZIP download** is the takeaway bag: all your dishes packed together in one trip.

You are never left waiting at the counter. You get a ticket immediately and can check
back as often as you like.

---

## 3. The journey of one request (step by step)

This is the full life of a single job, from your computer to a ZIP of PDFs.

1. **You send a request** — an HTTP `POST` containing the course name, who issued it,
   the date, and a list of recipients (each with a name and an optional email).

2. **The API validates the *shape* of the request** (Pydantic). Is there a course name?
   Is the recipient list actually a list? Is it empty? Is it larger than the allowed
   maximum (1,000)? If the request is fundamentally malformed, it is rejected right
   away with an error code **422** and nothing is created. This protects the system
   from nonsense input.

3. **The system creates a "job" and one row per recipient** in the database, all in a
   single transaction. Each recipient row starts out as `pending`. Rows that are
   obviously invalid (blank name, broken email) are marked `failed` immediately with a
   clear reason — but they **do not stop the job**.

4. **You get an instant response** — HTTP **202 Accepted** (which means "received, I'm
   working on it") plus a `Location` header that is the URL of your job. You can now
   poll that URL.

5. **The job is queued, then a worker starts cooking.** The API publishes the job to the
   Redis queue and returns immediately; a separate **Celery worker** picks it up and hands
   the certificates to a single shared, bounded pool of render threads. Because each
   certificate is committed to the database the moment it finishes, anyone polling the job
   sees the progress counter climb in real time — it never looks "stuck" until the very end.

6. **Each certificate is rendered independently.** If rendering one certificate throws
   an error, only *that* row is marked `failed` with the reason; every other
   certificate is unaffected. This is deliberate: one bad apple must not spoil the batch.

7. **When the last recipient is done**, the job is marked `completed` (everything
   worked) or `completed_with_failures` (some, but not all, worked).

8. **You download the results** — either a single PDF, or every generated PDF as one
   ZIP archive. Filenames are sanitised and numbered (`certificate-001-Ada-Lovelace.pdf`)
   so they are safe on every operating system.

---

## 4. Under the hood (technical)

### The stack

| Layer | Technology | Why |
| --- | --- | --- |
| Web framework | **FastAPI** | Fast, modern, auto-generates interactive docs, first-class async support |
| Data layer | **SQLAlchemy 2 + PostgreSQL 16** | Mature ORM, transactional integrity, real concurrency |
| Validation | **Pydantic v2** | Declarative input rules; rejects bad requests before they touch logic |
| Task queue | **Celery + Redis** | Jobs survive an API restart and can scale to more workers independently |
| Migrations | **Alembic** | Versioned, reviewable schema changes instead of ad-hoc table creation |
| PDF engine | **ReportLab** | Programmatic, pixel-level control over the certificate layout |
| Server | **Uvicorn** (2 worker processes) | Production ASGI server |
| Packaging | **Docker + Compose** | One command to run the whole system anywhere |

### Where the code lives

```
app/
  main.py                 # App entrypoint, CORS, startup, /health
  config.py               # All settings, read from environment variables
  security.py             # Optional API-key dependency (X-API-Key)
  celery_app.py           # Celery app + queue configuration
  database.py             # Database engine + session factory
  models.py               # The two database tables (Job, Certificate)
  schemas.py              # Request/response shapes + validation rules
  utils.py                # Small shared helpers (UTC time)
  routers/certificates.py # All the HTTP endpoints
  services/
    tasks.py              # The Celery task that runs a job
    processor.py          # The "kitchen": rendering orchestration + shared thread pool
    generator.py          # Draws one certificate onto a canvas -> PDF bytes
    validation.py         # Per-recipient cleaning and checking
alembic/                  # Versioned schema migrations
tests/                    # 29 automated tests
```

The structure follows a simple rule: **HTTP concerns** live in `routers/`, **business
logic** lives in `services/`, and **data shapes** live in `models.py`/`schemas.py`.
Each file has one job, which makes the system easy to reason about and to test.

### The three levels of concurrency

There are **three separate layers** of parallelism, and it is worth not confusing them:

- **`WEB_CONCURRENCY=2`** — two *Uvicorn processes* serving HTTP. This lets the API keep
  answering requests while one process is busy.
- **Celery `--concurrency=4`** — up to four *worker processes* pulling jobs off the queue.
  The queue can be given more workers (even on other machines) without touching the API.
- **`MAX_WORKERS=4`** — up to four *threads* inside each worker rendering PDFs. Rendering
  is independent per recipient, so a small shared pool per worker is ideal; it is created
  lazily and shut down cleanly when the process stops.

That gives the system resilience: a slow PDF does not block the API, a burst of jobs waits
its turn on the queue instead of exhausting memory, and a crashed worker does not lose work
— an unacknowledged job is redelivered.

---

## 5. The data model

Only **two tables** store everything. This is a deliberate choice — simple beats clever.

### `certificate_jobs` — one row per request you send

| Column | Meaning |
| --- | --- |
| `id` | Unique job reference (a UUID, e.g. `c6f42c70-…`) |
| `status` | `queued` → `processing` → `completed` / `completed_with_failures` / `failed` |
| `course_name`, `issued_by`, `issued_on` | Details printed on every certificate |
| `total_recipients` | How many people you submitted |
| `processed_recipients` | How many are finished (success **or** failure) |
| `succeeded_recipients` | How many PDFs were produced |
| `failed_recipients` | How many were rejected or errored |
| `created_at`, `updated_at`, `completed_at` | Timestamps (stored in UTC) |

### `certificates` — one row per recipient

| Column | Meaning |
| --- | --- |
| `id` | Unique certificate reference |
| `job_id` | Which job it belongs to (deleted automatically if the job is deleted) |
| `recipient_index` | Its original position in your list (keeps order stable) |
| `full_name`, `email` | The cleaned values we received |
| `status` | `pending` / `generated` / `failed` |
| `error` | The human-readable reason when `failed` |
| `file_path` | Where the PDF is stored on disk |
| `generated_at`, `created_at` | Timestamps |

**Why this shape?** The job table answers "how is my batch doing?" with a single row
lookup. The certificates table answers "what happened to each individual person?".
Separating them means progress counts are cheap and per-person detail is complete.

---

## 6. The lifecycle of a job

A job moves through a small, well-defined set of states. Nothing is ambiguous.

```
        ┌─────────┐   worker picks it up   ┌────────────┐
  POST  │ queued  │ ─────────────────────► │ processing │
 ──────►└─────────┘                        └─────┬──────┘
                                                 │ all recipients done
                       ┌─────────────────────────┼──────────────────────────┐
                       ▼                         ▼                          ▼
                ┌───────────┐        ┌──────────────────────────┐   ┌────────┐
                │ completed │        │ completed_with_failures  │   │ failed │
                └───────────┘        └──────────────────────────┘   └────────┘
                 every PDF ok          some ok, some rejected          crash
```

- **`completed`** — every recipient produced a PDF.
- **`completed_with_failures`** — some recipients were rejected or errored; the rest are fine.
- **`failed`** — the whole job could not be processed at all (unexpected crash).

A neat safety detail: the worker only adopts a job that is **still `queued`**. If the
same task were accidentally delivered twice, the second run simply does nothing. This
is a lightweight form of **idempotency** — a fancy word for "safe to run more than once."
Because the queue acknowledges a task only after it finishes, a worker that crashes
mid-job has its task redelivered rather than lost.

---

## 7. API reference

Base URL when running locally: `http://localhost:8000`

| Method | Path | What it does |
| --- | --- | --- |
| `POST` | `/api/v1/certificate-jobs` | Submit a bulk job → **202** + `Location` |
| `GET` | `/api/v1/certificate-jobs/{job_id}` | Job status, counters, `progress_percent` |
| `GET` | `/api/v1/certificate-jobs/{job_id}/certificates` | Per-recipient results (optionally `?status=`) |
| `GET` | `/api/v1/certificates/{certificate_id}/download` | Download one PDF (**409** if not ready) |
| `GET` | `/api/v1/certificate-jobs/{job_id}/download` | All generated PDFs as one **ZIP** |
| `GET` | `/health` | Liveness probe (used by Docker's health check) |

There is also **auto-generated interactive documentation** at `/docs` (Swagger UI) and
`/redoc`. These are produced straight from the code, so they are always accurate — an
examiner can click through and try every endpoint live. They can be switched off in
production with `ENABLE_DOCS=false`.

When `API_KEY` is set, every API endpoint under `/api/v1` requires an `X-API-Key`
header. Leaving `API_KEY` empty disables authentication entirely, which is the convenient
default for local development. `/health` stays open so orchestrators can probe liveness.

### Why these HTTP status codes matter

Choosing the right status code is part of good API design, and each one here is
intentional:

- **202 Accepted** — "I received it, work continues in the background." Not 200, because
  the work is *not* finished when the response returns.
- **401 Unauthorized** — "Authentication is enabled and you did not supply a valid key."
- **409 Conflict** — "You asked for something that isn't ready yet" (e.g. downloading a
  certificate that hasn't finished generating).
- **404 Not Found** — "No such job or certificate."
- **422 Unprocessable Entity** — "Your request was structurally invalid" (missing course
  name, empty list, too many recipients, a job id that isn't a valid UUID).

---

## 8. Example: submit a job and download the ZIP

**Request body** (a ready-made copy is in `examples/request.json`):

```json
{
  "course_name": "Advanced Python Programming",
  "issued_by": "Arena Academy",
  "issued_on": "2026-10-11",
  "recipients": [
    { "full_name": "Priya Sharma", "email": "priya@example.com" },
    { "full_name": "Rahul Verma", "email": "rahul@example.com" },
    { "full_name": "Aisha Khan", "email": "aisha@example.com" },
    { "full_name": "   ",           "email": "blank-name@example.com" },
    { "full_name": "Bad Email",     "email": "not-an-email" }
  ]
}
```

Note the last two entries: a blank name and an invalid email. They are **included on
purpose** to show the system's behaviour — they will be flagged individually, not crash
the batch.

**Command (curl):**

```bash
curl -i -X POST http://localhost:8000/api/v1/certificate-jobs \
  -H "Content-Type: application/json" \
  --data @examples/request.json
```

**Response (trimmed):**

```http
HTTP/1.1 202 Accepted
Location: /api/v1/certificate-jobs/c6f42c70-414e-440a-99cb-1037fd21b61e
```

```json
{
  "id": "c6f42c70-414e-440a-99cb-1037fd21b61e",
  "status": "completed_with_failures",
  "total_recipients": 5,
  "processed_recipients": 5,
  "succeeded_recipients": 3,
  "failed_recipients": 2,
  "progress_percent": 100.0,
  "certificates_url": "/api/v1/certificate-jobs/c6f42c70-414e-440a-99cb-1037fd21b61e/certificates"
}
```

Then download the batch:

```bash
curl -o certificates.zip \
  http://localhost:8000/api/v1/certificate-jobs/c6f42c70-414e-440a-99cb-1037fd21b61e/download
```

The ZIP contains three valid files; the two bad rows are reported with reasons instead
of files:

```
certificate-001-Priya-Sharma.pdf
certificate-002-Rahul-Verma.pdf
certificate-003-Aisha-Khan.pdf
```

---

## 9. Validation and error handling — the core philosophy

The single most important design decision in this project is this:

> **A bad input should fail *by itself*, loudly and clearly — never take the whole job
> down with it.**

There are two distinct kinds of "bad input", and they are treated very differently:

**1. Structurally broken requests → reject the whole thing (422).**
If the request as a whole doesn't make sense — no course name, an empty recipients list,
recipients that aren't even objects, or more than 1,000 rows — it is refused outright.
Generating a "partial" job from nonsense has no value, so we fail fast.

**2. Bad rows *inside* a valid request → flag just that row (per-recipient failure).**
If the overall request is well-formed but one person has a blank name or a malformed
email, that single certificate is marked `failed` with a plain-English reason, and
everyone else still gets a certificate. The job ends as `completed_with_failures`.

**Why?** In the real world, a 500-person list will almost always contain a few typos. If
one typo voided the entire batch, the tool would be useless. Graceful degradation is
what makes this trustworthy at scale.

Validation rules currently enforced:

- **Names** are whitespace-normalised (`"  Ada   Lovelace "` → `"Ada Lovelace"`) and
  limited to 120 characters.
- **Emails** are optional; if present they must look like a real address. A basic but
  effective pattern is used, which catches the common mistakes without rejecting valid
  addresses.
- **Recipient count** is capped at 1,000 per job (configurable).

---

## 10. The generated certificate

Each PDF is drawn from a single, fixed template — consistency is a feature when issuing
credentials. The layout is A4 **landscape** and includes:

- A decorative double gold border and a cream background.
- The word **CERTIFICATE** and **OF COMPLETION**.
- The recipient's name in an elegant italic serif, with a rule line beneath it.
- The course name, issuing organisation, and the date (e.g. `11 October 2026`).
- A circular seal motif and an "Authorised Signatory" line.
- A footer with the **certificate ID and issue date** — useful for traceability, so any
  certificate can be matched back to a database record.

A small but real touch of engineering: **long names automatically shrink to fit** the
page while staying fully intact (they are never truncated or clipped). This is handled
by measuring the rendered text width and stepping the font size down until it fits.

---

## 11. Reliability, security and production-readiness

These are the details that separate a demo from something deployable.

**Reliability**
- **Durable queue** — jobs are published to Redis and acknowledged only after they finish
  (`acks_late` + `reject_on_worker_lost`), so a worker that dies mid-job gets the task
  redelivered instead of silently dropping it.
- **Per-certificate commits** — progress is written to the database as each certificate
  completes, so pollers see live progress and a crash never loses the work already done.
- **Failure isolation** — one recipient's error is caught and recorded; it never
  interrupts the others (there is an automated test that proves this).
- **Immutable tasks** — worker threads receive a frozen, read-only snapshot of the data
  they need and hand back raw bytes. All database writes happen on a single orchestration
  thread, avoiding messy concurrent database access.
- **Shared, bounded pool** — every job in a worker shares one thread pool (created lazily,
  shut down on exit), so a burst of concurrent jobs cannot spawn unbounded threads.
- **Health checks** — Compose starts the API only after the database and Redis are healthy,
  and starts the worker only after the API is healthy.
- **`pool_pre_ping`** — connections are tested before use, so stale database connections
  are transparently replaced.

**Security**
- The container runs as a **non-root user** (`app`), limiting the damage any compromise
  could do.
- **Optional API-key auth** (`X-API-Key`) can guard every endpoint; it is a single
  dependency, so it protects new routes automatically.
- **Input validation at the trust boundary** (Pydantic) rejects malformed data before it
  reaches business logic — a first line of defence.
- **Sanitised error messages** — a rendering failure is stored and returned as the generic
  `Generation failed.`, while the full traceback goes only to the worker log. Internal
  details are never leaked through the API.
- **Output filenames are sanitised** — names are reduced to safe characters before being
  used in ZIP entries and download headers, preventing path or header injection.
- **Parameterised database access** via the ORM guards against SQL injection.
- **Config via environment variables** — no secrets are hard-coded; `.env` is git-ignored.

**Production-readiness**
- **Versioned migrations** (Alembic) applied automatically on startup — schema changes are
  reviewable and reproducible rather than implicit.
- **Streamed downloads** — the ZIP is built into a spooled temporary file (memory up to a
  threshold, disk beyond), so large batches never load fully into RAM.
- Pinned dependency versions for reproducible builds.
- A dedicated **test stage** in the Dockerfile so tests run in the exact runtime image.
- Clean separation of build stages keeps the final image small.
- `.dockerignore` keeps junk (tests, caches, local databases) out of the image.

---

## 12. Running it

### With Docker (recommended — this is the supported deployment)

```bash
docker compose up --build -d
```

- API: <http://localhost:8000>
- Interactive docs: <http://localhost:8000/docs>

This starts four services — `web`, `worker`, `db` and `redis` — and applies database
migrations automatically on startup.

```bash
docker compose ps            # check status
docker compose logs -f web    # follow the API logs
docker compose logs -f worker # follow the generation logs
docker compose down          # stop (keeps the database and stored PDFs)
docker compose down -v       # stop and wipe the database + stored PDFs
```

### Locally (no Docker, uses SQLite)

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload
```

With no configuration the app uses a local SQLite file and a local storage folder — zero
external setup. Set `DATABASE_URL` to point at PostgreSQL instead. To run without a broker,
set `CELERY_TASK_ALWAYS_EAGER=true` so jobs execute inline; otherwise run Redis and a
worker (`celery -A app.celery_app.celery_app worker --loglevel=info`).

### Configuration

Everything is controlled by environment variables (see `.env.example`):

| Variable | Default | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///./certificates.db` | Database connection |
| `STORAGE_DIR` | `./storage/certificates` | Where PDFs are written |
| `MAX_RECIPIENTS_PER_JOB` | `1000` | Maximum recipients in one job |
| `MAX_WORKERS` | `4` | Render threads within a worker |
| `CORS_ORIGINS` | `*` | Comma-separated allowed browser origins |
| `WEB_CONCURRENCY` | `2` | Uvicorn worker processes |
| `PORT` | `8000` | HTTP port |
| `API_KEY` | *(empty)* | Required `X-API-Key`; empty disables auth |
| `ENABLE_DOCS` | `true` | Expose `/docs`, `/redoc`, `/openapi.json` |
| `CELERY_BROKER_URL` | `redis://localhost:6379/0` | Queue broker |
| `CELERY_RESULT_BACKEND` | *(empty)* | Optional Celery result backend |
| `CELERY_TASK_ALWAYS_EAGER` | `false` | Run tasks inline (no broker; dev/tests) |

---

## 13. Testing

There are **29 automated tests**, split between end-to-end API behaviour and unit tests
of the core logic. They run against an isolated database and storage folder created
fresh for every run, so they never touch your real data. Celery runs in **eager mode** in
the tests, so the full request → task → database flow is exercised without a live broker.

```bash
pytest -v            # local
make test            # in a Docker container, in the real runtime image
```

What they cover:

- The health endpoint responds correctly.
- A valid job returns **202** with a `Location` header and the expected counters.
- A full job generates a certificate for every valid recipient.
- Structurally broken requests (empty list, wrong types, blank course name, malformed job
  id) are rejected with **422**.
- Too many recipients (over 1,000) is rejected with a helpful message.
- **Bad rows fail individually while good rows succeed** — the graceful-degradation rule.
- **A single rendering failure does not block the rest** (simulated with a fake failure).
- Downloads return a genuine PDF (`%PDF` header, correct content type) and a ZIP
  containing exactly the generated files.
- Requesting a ZIP with no generated certificates returns **409**.
- Unknown job/certificate ids return **404**.
- The PDF is a valid single-page document containing the expected text.
- Very long names are shrunk to fit but kept whole.
- Names are whitespace-normalised and bad values are rejected with a `Rejected:` reason.
- Progress is committed *incrementally* — while the first certificate is still rendering,
  the job is already `processing`, proving live updates work.
- **API-key auth** — with a key configured, requests without it get **401** and the correct
  key passes; with no key configured, everything is allowed.

A worthwhile note for a technical examiner: the incremental-progress test uses a **spy**
that pauses mid-render and inspects the database, so it genuinely proves real-time
progress rather than merely asserting it.

---

## 14. Design decisions and trade-offs (the "what I chose and why")

Good engineering is as much about what you *don't* build. These are the conscious choices.

| Decision | Reasoning | Trade-off / when to revisit |
| --- | --- | --- |
| **Celery + Redis task queue** | Jobs survive an API restart and the worker tier scales independently of the web tier. | One more service to operate (Redis). For a single-instance demo, `CELERY_TASK_ALWAYS_EAGER=true` runs tasks inline with no broker. |
| **Two tables, no more** | The minimal schema that answers every question asked of it. | Additional entities (users, templates, audit logs) would be added as real needs appear. |
| **Per-recipient failure isolation** | Faithful to how real bulk lists behave; one typo must not void 500 certificates. | Slightly more bookkeeping (a failed-row count) — worth it. |
| **Thread pool, not processes, for rendering** | ReportLab rendering is well-suited to threads and avoids process overhead. | If rendering becomes CPU-bound under heavy load, switch to a process pool or a worker queue. |
| **Alembic migrations** | Versioned, reviewable schema changes with an explicit upgrade path. | A migration must run before the app serves; the Docker image does this automatically on startup. |
| **Synchronous SQLAlchemy** | Simpler, easier to reason about and test. | Async I/O (e.g. `asyncpg`) would raise throughput under very high concurrency; not needed at this scale. |
| **UUID primary keys** | Non-guessable, safe to expose in URLs, no coordination needed across processes. | Slightly larger than integers; irrelevant here. |

---

## 15. Honest limitations and future work

Stating limitations confidently is a sign of understanding, not weakness.

- **Single shared API key** — authentication is one static key, not per-user accounts with
  roles. OAuth2 or scoped keys with per-tenant limits would be the natural next step.
- **Fixed single template** — every certificate looks the same. Multi-template support
  (logos, custom fields, and branding per customer) is an obvious extension.
- **No email delivery** — the API hands you files; it does not yet email them to
  recipients. This is a natural follow-on feature.
- **No built-in retry of a failed row** — a crashed worker redelivers its task, but a job
  that ends `completed_with_failures` must be resubmitted to retry the failed entries.

---

## 16. Glossary (for non-technical readers)

- **API** — a way for two programs to talk to each other. Here, a website or app sends a
  request and gets data back.
- **Endpoint** — one specific "address" on the API that does one thing (e.g. "create a job").
- **Background task** — work the system does *after* it has already replied to you, so
  you never have to wait.
- **Database** — the organised filing cabinet where all jobs and certificates are recorded.
- **PDF / ZIP** — a document format / a bundle of files compressed into one.
- **Job** — one bulk request: a course plus a list of recipients.
- **Validation** — checking that the information you sent makes sense before using it.
- **Docker / container** — a way to package the whole system so it runs the same way on
  any computer.
- **UUID** — a long random identifier (like `c6f42c70-…`) used instead of simple numbers
  so references cannot be guessed.

---

## 17. One-paragraph summary (for a report or slide)

> The Bulk Certificate Generator is a production-oriented HTTP API that converts a single
> request containing many recipients into one PDF certificate per valid recipient. It
> validates all input at the boundary, tolerates bad rows without failing the batch, and
> dispatches generation to a Celery worker over Redis so jobs survive restarts and scale
> independently; within a worker a shared bounded thread pool renders documents with live
> progress. Downloads are streamed, single PDFs or ZIPs alike, and an optional API key
> guards every endpoint. Built with FastAPI, SQLAlchemy 2, Pydantic v2 and ReportLab,
> persisted in PostgreSQL with versioned Alembic migrations, and deployed as a non-root
> Docker image with health checks and a 29-test suite, it is small, readable, and
> deliberately architected so the next steps — per-user auth, templates, and email — can be
> added without a rewrite.
