# VIORA-AI

**Intelligent Visual Learning Platform** — requirements and development baseline, 23 November 2026.

VIORA-AI turns lesson notes, topic descriptions and text-based PDFs into short animated explanations. A teacher checks the generated outline before the system builds the visuals. Students get a narrated lesson, captions, revision notes and questions tied to what was taught.

The point is to explain a concept visually, not to turn paragraphs into slides. A force-and-motion lesson, for example, should show how changing force or mass changes acceleration.

## What we're building

The first release covers the five online-round requirements in the hackathon brief:

1. **FR-001 — Content understanding:** extract concepts, objectives, relationships and teaching order from educational content.
2. **FR-002 — Animated video:** convert the approved outline into diagrams, scene instructions and rendered animations.
3. **FR-003 — Narration and synchronization:** generate speech, timed keywords and captions.
4. **FR-004 — Summary:** produce key points, applicable formulas and quick revision notes.
5. **FR-005 — Assessment:** generate checked questions, score responses and explain the answers.

The brief also lists six offline-round capabilities (FR-006–011): learning-level adaptation, multilingual voice, accessibility, checkpoints, offline export and teacher editing. These are planned as extensions to the same lesson format, not separate products. FR-012–016 cover the larger institution platform proposed for VIORA-AI; they are **not** part of the original hackathon requirements.

## How a lesson moves through the system

```text
Teacher uploads material
    -> source registered and processed
    -> concepts + lesson outline
    -> teacher checks and approves the outline
    -> scene plan + diagrams + animations
    -> narration, timing and captions
    -> playable lesson + summary + quiz
    -> student watches, revises and answers
```

Generated artifacts keep references to the source and to the exact version used. Editing an outline or narration creates a new version and marks affected outputs for regeneration. We keep rendering restricted to validated scene data; we do not execute model-generated source code.

## Initial technology choices

The installable baseline is **Python 3.13.16, FastAPI 0.165.0, React 19.3.1, Vite 8.3.8, TypeScript 7.0.2, PostgreSQL 17.12 and Remotion 4.0.545**. The renderer is one deliberate choice, not the only engine the architecture could ever support.

| Part | Planned tool | Notes |
|---|---|---|
| API and AI orchestration | Python, FastAPI, Pydantic | Typed APIs and validation |
| Data and background jobs | PostgreSQL, SQLAlchemy, Alembic | Start with database-backed durable jobs; no Redis requirement for the first build |
| PDF processing | pdfplumber, pypdf | Text PDFs only; no OCR in the first release |
| Web app | React, TypeScript, Vite | Teacher workflow and student player |
| Video | Remotion, FFmpeg | Controlled React scene templates, not unrestricted AI-generated scripts |
| AI and speech | Provider adapter, OpenAI Python SDK as initial integration | Credentials and per-call costs required; model and voice IDs selected during integration |

Package versions, commands, alternatives and licensing notes are in [TECH-STACK.md](docs/TECH-STACK.md). Start there before installing anything.

## Developer setup — dependency baseline

The repository currently contains **requirements, architecture and install manifests**, not a working API or UI implementation. These commands install the intended dependencies. Application commands will become available after the source folders are implemented.

Requirements: Python 3.13.16, Node.js 24.22.3 LTS, npm, Docker Desktop with Compose, and FFmpeg. On Windows, use PowerShell with a Python 3.13 virtual environment; enable Docker's Linux container backend.

```powershell
# From the repository root; Windows PowerShell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt
npm --prefix web install
Copy-Item .env.example .env
# Set a unique local POSTGRES_PASSWORD in .env, then:
docker compose up -d db
```

On Linux/macOS, activate with `source .venv/bin/activate`, copy `.env.example` to `.env`, and use `python3.13` instead of `py -3.13`.

Verify installations with `python --version`, `node --version`, `npm --prefix web list --depth=0`, `ffmpeg -version` and `docker compose ps`. Do not place real API keys in Git.

**Version policy:** package manifests pin direct dependencies; commit the generated `web/package-lock.json` after the first successful `npm install`. The Python list pins direct dependencies, **not transitive hashes**; create a resolved lock for your target platform when development begins. A registry version check is not a successful integration test.

## Project documents

- [Requirements](docs/REQUIREMENTS.md) — all FRs, quality constraints and the installable stack summary.
- [Technology stack](docs/TECH-STACK.md) — exact selected versions, setup, alternatives and license risks.
- [System architecture](docs/ARCHITECTURE-V4-FULL.md) — the V4 modules, trust boundaries and lesson artifact flow.
- [Development plan](docs/DELIVERY-PLAN.md) — build order, dependencies and test gates.
- [Decision log](docs/DECISIONS.md) — approved requirements and technical choices still to validate.
- [FR-001](docs/FR-001-CONTENT-UNDERSTANDING.md), [FR-002](docs/FR-002-ANIMATED-VIDEO.md), [FR-003–005](docs/FR-003-TO-005.md), [FR-006–011](docs/FR-006-TO-011-OFFLINE-ROUND.md), [FR-012–016](docs/FR-012-TO-016-PLATFORM.md) — details and acceptance tests.

## Current status

FR-001's functional baseline and its five design decisions were approved. The remaining FRs have complete proposed designs for planning and are **not implemented**. The versions above were checked against published release pages on 23 November 2026; cross-package installation, operating-system compatibility, renderer licensing and provider behavior still need hands-on validation.

The source brief is *Intelligent Visual Learning Platform* (Smart Education), provided with the project. It is the basis of the online and offline feature split. The institutional modules are VIORA-AI extensions.
