# Technology stack and installation

**Checked: 23 November 2026.** This page fixes a starting toolchain for the first VIORA-AI development sessions. It is a version baseline, **not a claim that the applications or their integrations have been tested together**. Record the resolved lockfiles and any necessary updates after the first clean installation.

## 1. What we will use first

| Layer | Selection | Tested use in VIORA-AI | Why |
|---|---|---|---|
| Backend language | **Python 3.13.16** | Planned | Mature Python runtime; use this instead of whichever Python happens to be installed |
| Backend API | **FastAPI 0.165.0** + **Uvicorn 0.54.0** | Planned | API endpoints, upload requests, job status, auth boundaries |
| Validation | **Pydantic 2.14.4** | Planned | Lesson-outline, scene, question and API request schemas |
| Persistence | **PostgreSQL 17.12** | Planned | Tenants, documents, artifact versions, teacher decisions, quizzes, durable jobs |
| Database code | **SQLAlchemy 2.1.9**, **Alembic 1.20.1**, **psycopg 3.3.9** | Planned | ORM, migrations and PostgreSQL driver |
| PDF handling | **pdfplumber 0.11.11**, **pypdf 7.1.1** | Planned | Layout positions plus baseline PDF parsing; not OCR |
| Web runtime | **Node.js 24.22.3 LTS** | Planned | UI tooling and Remotion render process |
| Web interface | **React 19.3.1** + **Vite 8.3.8** | Planned | Upload/review forms, status, lesson viewer and quiz |
| Language | **TypeScript 7.0.2** | Planned | Typed UI and scene-rendering components |
| Video templates | **Remotion 4.0.545** | Planned, license review required | Deterministic animation and segment rendering from validated data |
| Media | **FFmpeg 8.1.x** | Planned | Probe and compose media; platform-specific installation |
| AI integration | **OpenAI Python SDK 3.57.0** | Planned, credentials required | First implementation of a swappable content/TTS provider interface |
| Local database | **Docker Compose**, image **`postgres:17.12`** | Planned | Shared DB setup without installing PostgreSQL directly |

Selecting the toolchain does not select an LLM model, voice, provider contract, hosting platform, pricing plan or AI output-quality threshold. We will confirm each of those with short integration tests.

### Rendering choice and license

Remotion is a good fit for scene templates because the student web player and video renderer can share React components. **Check its license before team or commercial use.** Its published licensing terms distinguish individual/small-team use from other organizations and can require a company license. If those terms do not fit the project, keep the typed `SceneSpec` contract and switch the renderer to a licensed SVG/Canvas/FFmpeg implementation. Do not make the video pipeline depend on Remotion-specific JSON.

## 2. Backend dependencies

The same versions are written in [`backend/requirements.txt`](../backend/requirements.txt).

| Package | Exact version | Reason |
|---|---|---|
| `fastapi` | `0.165.0` | Routes and OpenAPI |
| `uvicorn[standard]` | `0.54.0` | Development server |
| `pydantic` | `2.14.4` | Input/output contracts |
| `SQLAlchemy` | `2.1.9` | Database operations |
| `alembic` | `1.20.1` | Schema migrations |
| `psycopg[binary]` | `3.3.9` | PostgreSQL connector |
| `pdfplumber` | `0.11.11` | PDF text with page/position information |
| `pypdf` | `7.1.1` | PDF metadata and basic handling |
| `python-multipart` | `0.0.34` | File upload parsing |
| `PyJWT` | `2.15.1` | JWT support if local session tokens are used |
| `pwdlib[argon2]` | `0.4.2` | Password hashing for local user accounts |
| `openai` | `3.57.0` | First AI provider adapter |
| `httpx` | `0.28.1` | Async HTTP requests and API tests |
| `python-dotenv` | `1.2.4` | Local environment loading |
| `pytest` | `9.2.0` | Test suite |
| `pytest-asyncio` | `1.5.0` | Async tests |
| `ruff` | `0.17.6` | Formatting and linting |

**Dependency policy:** these are direct pins. They do not replace a complete lockfile. After a clean install and smoke tests, generate a platform-compatible lock or compiled constraints with hashes. Review dependency alerts before upgrading any package. Do not install conflicting `multipart` packages; FastAPI expects `python-multipart`.

## 3. Web and video dependencies

Everything below is in [`web/package.json`](../web/package.json), with exact versions rather than floating `latest` ranges.

| Package | Exact version | Use |
|---|---|---|
| `react`, `react-dom` | `19.3.1` | Web UI and scene components |
| `vite` | `8.3.8` | Web dev server and build tool |
| `typescript` | `7.0.2` | Typechecking |
| `@vitejs/plugin-react` | `6.3.0` | Vite React integration |
| `@types/react`, `@types/react-dom` | `19.3.0` | Type definitions |
| `remotion` | `4.0.545` | Frame-based animation API |
| `@remotion/player` | `4.0.545` | Playback of scene components |
| `@remotion/renderer` | `4.0.545` | Server-side rendering |
| `@remotion/cli` | `4.0.545` | Developer previews and render commands |
| `@remotion/bundler` | `4.0.545` | Bundling scene compositions for renders |

All Remotion packages need the **same patch version**. Use `npm install` from the `web` directory; commit `package-lock.json` after the team verifies the install. Later development uses `npm ci` against the committed lock, not an unpinned fresh resolution.

## 4. First-time setup

### Windows (PowerShell)

1. Install Python **3.13.16**, Node.js **24.22.3 LTS**, Docker Desktop (Linux containers) and an FFmpeg **8.1.x** binary. Add Python/Node/FFmpeg to `PATH` where necessary.
2. Open PowerShell in the repository root and run:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt
npm --prefix web install
Copy-Item .env.example .env
```

3. In `.env`, **change `POSTGRES_PASSWORD` and the matching password in `DATABASE_URL`**, set a random `JWT_SECRET`, and add an API key only when working on AI integration.
4. Start PostgreSQL:

```powershell
docker compose up -d db
docker compose ps
```

5. Check the tools:

```powershell
python --version
node --version
npm --prefix web list --depth=0
ffmpeg -version
docker compose ps
```

### Linux / macOS

Install the same runtime versions, then use `python3.13 -m venv .venv`, `source .venv/bin/activate`, `python -m pip install -r backend/requirements.txt`, `npm --prefix web install`, `cp .env.example .env` and `docker compose up -d db`.

**Current limitation:** this submission includes the dependency manifests but **not application source code**. Installing dependencies is possible; starting a functional API/React application or rendering a lesson is not possible until those modules are implemented. This separation prevents a setup file from being mistaken for a working scaffold.

## 5. Why we did not install everything

| Candidate | Current decision | When to reconsider |
|---|---|---|
| Django / Django REST Framework | Not in first build | If institutional admin screens outweigh AI/API workflows |
| Next.js | Not needed now | If server-rendered pages, SEO or a combined Node backend becomes important |
| Manim | Optional specialized renderer | If mathematical proofs and geometry need higher-fidelity symbolic animation |
| Native Canvas/SVG + FFmpeg | Fallback renderer | If licensing or rendering constraints rule out Remotion |
| PyMuPDF | Not installed | Compare on difficult PDFs after licensing review; current scope covered by pdfplumber |
| Docling / OCR | Deferred | When scanned PDFs, tables, scientific layout or formulas need deeper parsing |
| Redis / Celery | Deferred | When PostgreSQL job leases and worker polling are measurably insufficient |
| Vector DB / RAG service | Deferred | When permission-filtered cross-document retrieval is introduced and benchmarked |
| Object storage (S3/MinIO) | Storage abstraction first | Use cloud object storage when persistent shared deployments begin; local disk only for development |
| Multiple AI providers | Provider interface now, integrations later | Add provider after quality/cost/privacy comparison |
| Kubernetes / microservices | Not planned for P0 | Only if usage and team scale justify them |

**Job design:** in P0 a PostgreSQL table holds job states, leases, attempts, checkpoints and idempotency keys. A separate Python worker process claims jobs transactionally using row locks, writes results and releases/renews leases. This is durable **when implemented correctly**; the requirements manifest does not implement it. A transactional outbox can be introduced when downstream events need reliable delivery.

## 6. Compatibility checks for the first development session

Do not mark this stack as tested until the following pass on the actual team machines:

1. `pip install -r backend/requirements.txt` resolves on Windows and Linux using Python 3.13.16.
2. `npm install` resolves every exact version with Node 24.22.3, React 19.3.1 and Remotion 4.0.545.
3. API can connect to PostgreSQL 17.12; Alembic migration runs and rolls back.
4. A text PDF extracts page numbers, headings and positional text in a repeatable way.
5. A minimal typed scene renders to a playable video; FFmpeg probes its duration and codec.
6. A dummy TTS clip produces valid narration/caption timing and a scene does not outrun speech.
7. A worker restart preserves the accepted job; duplicate requests do not duplicate an artifact.
8. Verify Remotion licensing for the actual team/organization and obtain any required license.

If a pin fails, update the **manifest, this page and the decision log together**. Prefer a tested patch release over an arbitrary `latest` update.

## 7. Version sources

Checked against project-maintained release pages and registries on 23 November 2026 (release existence, **not** cross-package interoperability):

- [Python 3.13.16](https://www.python.org/downloads/release/python-31316/), [Node.js 24 LTS](https://nodejs.org/en/download/), [PostgreSQL 17.12](https://www.postgresql.org/docs/17/release-17-12.html), [PostgreSQL Docker tags](https://hub.docker.com/_/postgres).
- PyPI: [FastAPI](https://pypi.org/project/fastapi/), [Uvicorn](https://pypi.org/project/uvicorn/), [Pydantic](https://pypi.org/project/pydantic/), [SQLAlchemy](https://pypi.org/project/SQLAlchemy/), [Alembic](https://pypi.org/project/alembic/), [psycopg](https://pypi.org/project/psycopg/), [pdfplumber](https://pypi.org/project/pdfplumber/), [pypdf](https://pypi.org/project/pypdf/), [OpenAI SDK](https://pypi.org/project/openai/), [pytest](https://pypi.org/project/pytest/), [Ruff](https://pypi.org/project/ruff/).
- npm: [React](https://www.npmjs.com/package/react), [Vite](https://www.npmjs.com/package/vite), [TypeScript](https://www.npmjs.com/package/typescript), [Remotion](https://www.npmjs.com/package/remotion), [Remotion renderer](https://www.npmjs.com/package/@remotion/renderer), [Vite React plugin](https://www.npmjs.com/package/@vitejs/plugin-react).
- [FFmpeg downloads](https://ffmpeg.org/download.html), [Remotion terms and licensing](https://www.remotion.dev/docs/license/pricing).
