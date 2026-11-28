# VIORA-AI

### Intelligent Learning. Connected Education.

**VIORA-AI** is an AI-powered learning and academic management platform that bridges independent student learning with institution-managed education.

The platform combines a personalized AI learning environment, intelligent study tools, digital classrooms and institutional academic management into a unified ecosystem.

Rather than simply providing AI-generated answers, VIORA aims to transform educational content into meaningful, interactive and personalized learning experiences.

---

## Product Vision

Traditional educational platforms often separate classroom management, study resources, examinations and independent learning. VIORA brings these experiences together while maintaining a clear distinction between personal learning and official academics.

The platform follows an institution-managed **B2B2C model**, where educational institutions provide students and faculty with access to a shared academic ecosystem.

**One platform. Two learning environments.**

- **Personal Learning** — Independent, AI-powered learning tailored to the student.
- **My Institution** — Structured academic activities managed by the educational institution.

---

## Core Features

### Personal Learning Workspace

Students can independently explore academic topics, clarify doubts and learn from their own study materials without depending on teacher-uploaded content.

- Conversational AI tutor with contextual follow-up questions
- Independent topic exploration and explanations
- PDF, image and text-based study material uploads
- Automatic document extraction and OCR
- Material-grounded explanations with source references
- Mathematical equation and programming code rendering
- Persistent conversations and learning history
- Personal study material library
- Private learning sessions
- Responsive, conversation-first interface

### Intelligent Study Studio

VIORA transforms learning sessions and study materials into specialized learning resources.

| Learning Tool | Functionality |
|---|---|
| Study Notes | Structured explanations, definitions, formulas and revision notes |
| Academic Insights | Key concepts, relationships, misconceptions and important takeaways |
| Mind Maps | Interactive concept visualizations with expandable branches |
| Flashcards | Active recall, self-assessment and revision progress |
| Practice Quizzes | Difficulty-based questions, scoring and answer explanations |
| Practice Examinations | Timed assessments, autosaved answers and performance analysis |

Generated learning resources can be saved, reopened, regenerated and managed within their respective learning sessions.

### Institutional Management

VIORA extends beyond personal learning through an institution-controlled academic environment.

**College Administration**

- Student and faculty account onboarding
- Invitation-based account activation
- Academic profiles and searchable directories
- Department, programme, semester and section management
- Faculty responsibility assignment
- Role-based permissions and academic access management

**Faculty Workspace**

A single faculty account can independently hold two academic responsibilities:

- **Homeroom Teacher:** Manage assigned classes, student rosters and academic profiles.
- **Subject Teacher:** Manage assigned subject classrooms, enrolled students, learning materials and announcements.

Faculty responsibilities are assigned and revoked by authorized institutional administrators.

**Student Institutional Workspace**

- Personalized academic dashboard
- Assigned subjects and faculty information
- Homeroom teacher details
- Digital subject classrooms
- Published study materials
- Classroom announcements
- Academic profile

### Learn with VIORA AI

The platform connects classroom resources with independent AI learning.

Students can open authorized institutional study materials directly within their Personal Learning workspace and use VIORA's learning tools to understand, revise and practice the content.

Teachers retain control over their original materials, while students independently manage their personal learning activities.

---

## Platform Architecture

```text
                         VIORA-AI
                             |
             +---------------+---------------+
             |                               |
      Personal Learning               My Institution
             |                               |
      +------+-------+               +-------+-------+
      |      |       |               |       |       |
   AI Tutor Library Studio        Students Faculty Admin
                     |                       |
          +----------+----------+      Digital Classrooms
          |          |          |             |
        Notes     Mind Maps  Practice     Study Materials
       Insights   Flashcards Assessments  Announcements
```

VIORA uses a modular architecture designed to separate identity management, academic operations, content processing, AI orchestration and learning workflows.

### AI and Knowledge Processing

- Provider-independent AI gateway
- Cloudflare Workers AI integration
- Adaptive document extraction
- OCR-based text recognition
- Lexical and semantic retrieval
- Hybrid retrieval and knowledge graph support
- Source-aware academic explanations
- Structured AI output validation
- Bounded generation and resource-aware processing

### Security and Privacy

Security is integrated into the platform architecture through:

- Institution-scoped multi-tenancy
- Role-based and resource-scoped authorization
- PostgreSQL Row-Level Security
- Restricted database runtime roles
- Secure authentication and session management
- Argon2id password hashing
- Student-private learning resources
- Assignment-scoped faculty access
- Source access and publication controls
- Server-side assessment answer keys

---

## Technology Stack

| Layer | Technologies |
|---|---|
| Frontend | React, TypeScript, Vite |
| Backend | Python, FastAPI, Pydantic |
| Database | PostgreSQL, SQLAlchemy |
| Database Migrations | Alembic |
| AI Infrastructure | Cloudflare Workers AI, OpenAI-compatible provider adapters |
| Document Processing | PyPDF, pdfplumber, OCR |
| Knowledge Retrieval | PostgreSQL Full-Text Search, local embeddings, optional pgvector |
| Mathematical Rendering | KaTeX |
| Interactive Visualizations | React, SVG |
| Authentication | JWT, refresh-token sessions, Argon2id |
| API Contracts | OpenAPI, openapi-typescript |
| Testing | Pytest, PostgreSQL integration tests, frontend unit tests |
| Development Infrastructure | Docker, GitHub Actions |

---

## Repository Structure

```text
VIORA-AI/
|
|-- backend/
|   |-- viora/
|   |   |-- identity/
|   |   |-- authorization/
|   |   |-- institution/
|   |   |-- sources/
|   |   |-- knowledge/
|   |   |-- ai/
|   |   |-- workflow/
|   |-- migrations/
|   |-- tests/
|   |-- requirements.txt
|
|-- web/
|   |-- src/
|   |   |-- api/
|   |   |-- auth/
|   |   |-- components/
|   |   |-- features/
|   |   |-- tenant/
|   |   |-- styles/
|   |-- package.json
|
|-- cloudflare/
|   |-- viora-worker/
|
|-- contracts/
|   |-- http/
|
|-- docs/
|-- .github/
|   |-- workflows/
|-- compose.yaml
|-- .env.example
|-- README.md
```

---

## Development Roadmap

| Phase | Development Area | Status |
|---|---|---|
| R1 | Product realignment and application foundation | Completed |
| R2 | Independent AI learning workspace | Completed |
| R3 | Intelligent learning tools and student experience | Completed |
| R4 | Institutional management and digital classrooms | Implemented — consolidation in progress |
| R5 | Official examinations, evaluation and academic rankings | In progress |
| R6 | Animated visual learning and adaptive intelligence | Planned |
| R7 | Product refinement, optimization and deployment | Planned |

### Upcoming Capabilities

- Faculty-created official examinations
- Scheduled academic assessments
- Official marks and result publication
- Subject-wise academic rankings
- Homeroom academic performance monitoring
- AI-generated animated educational explanations
- Narration and synchronized captions
- Adaptive learning based on student understanding
- Multilingual learning experiences
- Accessibility and offline learning support

---

## Getting Started

### Prerequisites

- Python 3.13.16
- Node.js 24.22.3
- npm
- PostgreSQL 17
- Docker and Docker Compose
- AI provider credentials for live AI functionality

### 1. Clone the repository

```bash
git clone https://github.com/sanmuka-sai-12/VIORA-AI.git
cd VIORA-AI
```

### 2. Install dependencies

Windows PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt

npm --prefix web ci
```

### 3. Configure the environment

```powershell
Copy-Item .env.example .env
```

Configure the database connection, authentication signing key, storage configuration and AI provider credentials.

For Cloudflare Workers AI integration, the relevant environment variables include:

```env
AI_PROVIDER=cloudflare_worker
CLOUDFLARE_WORKER_URL=
CLOUDFLARE_WORKER_SECRET=
CLOUDFLARE_AI_MODEL=
```

Never commit real credentials or local environment files.

### 4. Set up PostgreSQL

```powershell
docker compose up -d db
```

Provision the database schema-owner credentials and a separate restricted application database role according to the [database provisioning and authorization guide](docs/authorization.md).

Configure `MIGRATION_DATABASE_URL` in the migration process environment before running:

```powershell
$env:PYTHONPATH = "backend"
alembic upgrade head
```

Apply the required application privileges and configure `DATABASE_URL` using the restricted runtime account.

Complete the controlled institution and development-account bootstrap before attempting to sign in.

### 5. Start the backend

```powershell
$env:PYTHONPATH = "backend"
uvicorn viora.main:create_app --factory --host 127.0.0.1 --port 8000
```

### 6. Start the frontend

In a separate terminal:

```powershell
npm --prefix web run dev
```

Open the application at:

```text
http://localhost:5173
```

The API documentation is available at:

```text
http://localhost:8000/docs
```

Background content-processing workflows may additionally require the workflow worker:

```powershell
$env:PYTHONPATH = "backend"
python -m viora.workflow.worker
```

---

## Testing and Code Quality

VIORA uses automated verification to maintain application stability and institutional isolation.

### Backend

```powershell
$env:PYTHONPATH = "backend"

ruff check .
pytest -m "not postgres"
```

### Frontend

```powershell
npm --prefix web test
npm --prefix web run build
```

### API Contract Generation

```powershell
python -m viora.export_contracts
npm --prefix web run codegen
```

PostgreSQL integration tests require a separately configured and migrated test database. Follow the authorization guide before running them.

Automated AI tests use controlled test providers rather than consuming live inference resources.

GitHub Actions verifies backend functionality, API contracts, database migrations, authorization behavior and frontend builds.

---

## Design Principles

- **Student-first:** Learning remains the primary experience.
- **Institution-connected:** Academic activities are organized through authorized institutional relationships.
- **Teacher-controlled:** Faculty retain control over official materials and assessments.
- **Privacy-aware:** Independent learning remains separate from official academic records.
- **Resource-efficient:** AI generation occurs on demand with bounded resource consumption.
- **Extensible:** Modular architecture supports future learning technologies without requiring complete system redesigns.

---

## Project Documentation

- [Requirements](docs/REQUIREMENTS.md)
- [Technology Stack](docs/TECH-STACK.md)
- [System Architecture](docs/ARCHITECTURE-V4-FULL.md)
- [Development Plan](docs/DELIVERY-PLAN.md)
- [Decision Log](docs/DECISIONS.md)
- [Authorization and Database Security](docs/authorization.md)

The original requirements and architecture documents preserve the project's initial design baseline. The current product direction expands that foundation into a unified personal learning and institutional education platform.

---

## Development Status

VIORA-AI is under active development.

Core personal learning capabilities and intelligent study tools are implemented. Institutional functionality is undergoing consolidation, followed by official academic assessments and advanced visual learning.

AI-generated educational content may contain inaccuracies. Image understanding currently focuses primarily on text extraction and OCR. Full animated lesson generation, adaptive intelligence and production deployment remain upcoming development milestones.

---

### VIORA-AI

**Learn independently. Connect academically. Understand visually.**
