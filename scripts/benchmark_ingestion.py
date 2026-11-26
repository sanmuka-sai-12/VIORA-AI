"""Measure Batch 2.2 source registration latency, format detection, extraction, and storage overhead."""

import json
import os
import secrets
import shutil
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema
from viora.authorization.public import AuthorizationService
from viora.config import AuthSettings, Settings
from viora.database_roles import grant_application_privileges
from viora.db import Database
from viora.identity.public import AuthService, IdentityAttributeKind
from viora.perf.public import (
    BenchmarkRunner,
    build_synthetic_corpus,
    percentile,
)
from viora.sources.extraction import (
    AdaptiveDocumentExtractor,
    validate_and_sniff_text_input,
    validate_and_sniff_upload,
)
from viora.sources.repository import SqlSourceRepository
from viora.sources.schemas import clamp_source_limits
from viora.sources.service import SourceIngestionService
from viora.storage.public import local_artifact_store

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    pw = ""
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("POSTGRES_PASSWORD="):
                pw = line.split("=", 1)[1].strip()
    raw_url = os.environ.get("VIORA_TEST_DATABASE_URL") or (
        f"postgresql+psycopg://viora:{pw}@127.0.0.1:5432/viora_test" if pw else ""
    )
    if not raw_url:
        raise SystemExit("VIORA_TEST_DATABASE_URL or .env POSTGRES_PASSWORD required")

    url = make_url(raw_url)
    corpus = build_synthetic_corpus()
    limits = clamp_source_limits()
    extractor = AdaptiveDocumentExtractor()
    runner = BenchmarkRunner()

    # 1. Measure format sniffing / detection + adaptive extraction latency (25 iterations each)
    iterations = 25
    text_sample = corpus.plain_text_bytes.decode("utf-8")
    detect_results = [
        runner.run(
            workload_name="detect_plain_text",
            fn=lambda: validate_and_sniff_text_input(
                input_format="typed_notes",
                content=text_sample,
                original_filename="notes.txt",
                limits=limits,
            ),
            iterations=iterations,
            mode="uncached",
            provider_kind="local_cpu",
            characteristics={"bytes": len(corpus.plain_text_bytes)},
        ),
        runner.run(
            workload_name="detect_small_pdf",
            fn=lambda: validate_and_sniff_upload(
                raw_bytes=corpus.small_pdf,
                declared_format="digital_pdf",
                original_filename="small.pdf",
                claimed_content_type="application/pdf",
                limits=limits,
            ),
            iterations=iterations,
            mode="uncached",
            provider_kind="local_cpu",
            characteristics={"bytes": len(corpus.small_pdf)},
        ),
        runner.run(
            workload_name="extract_plain_text",
            fn=lambda: extractor.extract(
                tenant_id=uuid4(),
                source_id=uuid4(),
                version_id=uuid4(),
                source_version=1,
                source_sha256="a" * 64,
                input_format="typed_notes",
                title="Notes",
                raw_bytes=corpus.plain_text_bytes,
                limits=limits,
            ),
            iterations=iterations,
            mode="uncached",
            provider_kind="local_cpu",
            characteristics={"bytes": len(corpus.plain_text_bytes)},
        ),
        runner.run(
            workload_name="extract_small_pdf_triage",
            fn=lambda: extractor.extract(
                tenant_id=uuid4(),
                source_id=uuid4(),
                version_id=uuid4(),
                source_version=1,
                source_sha256="b" * 64,
                input_format="digital_pdf",
                title="Small PDF",
                raw_bytes=corpus.small_pdf,
                limits=limits,
            ),
            iterations=iterations,
            mode="uncached",
            provider_kind="local_cpu",
            characteristics={"bytes": len(corpus.small_pdf), "pages": 1},
        ),
        runner.run(
            workload_name="extract_multipage_pdf_triage",
            fn=lambda: extractor.extract(
                tenant_id=uuid4(),
                source_id=uuid4(),
                version_id=uuid4(),
                source_version=1,
                source_sha256="c" * 64,
                input_format="digital_pdf",
                title="Multipage PDF",
                raw_bytes=corpus.multipage_pdf,
                limits=limits,
            ),
            iterations=iterations,
            mode="uncached",
            provider_kind="local_cpu",
            characteristics={"bytes": len(corpus.multipage_pdf), "pages": 12},
        ),
    ]

    # 2. End-to-end live PostgreSQL + ArtifactStore registration latency & storage overhead
    tmp_dir = Path(tempfile.mkdtemp(prefix="viora_bench_"))
    schema, role = f"bench_{uuid4().hex}", f"bench_app_{uuid4().hex}"
    role_pw = secrets.token_urlsafe(32)
    scoped = url.update_query_dict({"options": f"-csearch_path={schema}"})
    admin = create_engine(scoped)
    app_db = Database(
        Settings(
            database_url=SecretStr(
                scoped.set(username=role, password=role_pw).render_as_string(
                    hide_password=False
                )
            ),
            media_storage_dir=str(tmp_dir),
            auth=AuthSettings(signing_key=SecretStr("b" * 64)),
        )
    )
    e2e_report: dict[str, dict[str, float | int | str]] = {}
    try:
        with admin.begin() as conn:
            conn.execute(CreateSchema(schema))
            conn.connection.driver_connection.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT"
                ).format(sql.Identifier(role), sql.Literal(role_pw))
            )
        prev_db = os.environ.get("DATABASE_URL")
        prev_mig = os.environ.get("MIGRATION_DATABASE_URL")
        os.environ["DATABASE_URL"] = scoped.render_as_string(hide_password=False)
        os.environ["MIGRATION_DATABASE_URL"] = scoped.render_as_string(
            hide_password=False
        )
        try:
            cfg = Config(str(ROOT / "alembic.ini"))
            command.upgrade(cfg, "head")
        finally:
            if prev_db is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = prev_db
            if prev_mig is None:
                os.environ.pop("MIGRATION_DATABASE_URL", None)
            else:
                os.environ["MIGRATION_DATABASE_URL"] = prev_mig

        with admin.begin() as conn:
            grant_application_privileges(conn, role, schema)
            t_id, u_id, off_id = uuid4(), uuid4(), uuid4()
            yr_id, sem_id, sec_id, subj_id = uuid4(), uuid4(), uuid4(), uuid4()
            auth = AuthService(app_db.sessions, AuthSettings(signing_key=SecretStr("b" * 64)))
            pwh = auth.passwords.hash(SecretStr("bench passphrase 42"))
            conn.execute(text("INSERT INTO institutions VALUES (:t, 'Bench School', true)"), {"t": t_id})
            conn.execute(text("INSERT INTO users VALUES (:u, 'Bench Teacher', 'active')"), {"u": u_id})
            conn.execute(text("INSERT INTO memberships VALUES (:t, :u, 'active')"), {"t": t_id, "u": u_id})
            conn.execute(text("INSERT INTO role_grants (tenant_id, user_id, role) VALUES (:t, :u, 'subject_teacher')"), {"t": t_id, "u": u_id})
            conn.execute(text("INSERT INTO verified_identity_attributes VALUES (:u, 'email', 'bench@example.test', now())"), {"u": u_id})
            conn.execute(text("INSERT INTO password_credentials VALUES (:u, :h)"), {"u": u_id, "h": pwh})
            conn.execute(text("INSERT INTO academic_years (tenant_id, id, name) VALUES (:t, :y, '2026')"), {"t": t_id, "y": yr_id})
            conn.execute(text("INSERT INTO semesters (tenant_id, id, name, academic_year_id) VALUES (:t, :s, 'S1', :y)"), {"t": t_id, "s": sem_id, "y": yr_id})
            conn.execute(text("INSERT INTO sections (tenant_id, id, name, semester_id) VALUES (:t, :sec, 'Sec1', :s)"), {"t": t_id, "sec": sec_id, "s": sem_id})
            conn.execute(text("INSERT INTO subjects (tenant_id, id, name) VALUES (:t, :sub, 'Math')"), {"t": t_id, "sub": subj_id})
            conn.execute(text("INSERT INTO subject_offerings (tenant_id, id, name, section_id, subject_id) VALUES (:t, :o, 'Math-101', :sec, :sub)"), {"t": t_id, "o": off_id, "sec": sec_id, "sub": subj_id})
            conn.execute(text("INSERT INTO teacher_assignments VALUES (:t, :u, :o, true)"), {"t": t_id, "u": u_id, "o": off_id})

        token = auth.login(
            IdentityAttributeKind.email, "bench@example.test", SecretStr("bench passphrase 42")
        ).access_token.get_secret_value()
        gate = AuthorizationService(
            app_db,
            AuthService(app_db.sessions, AuthSettings(signing_key=SecretStr("b" * 64))),
        )
        store = local_artifact_store(tmp_dir, max_artifact_bytes=limits.max_bytes)
        service = SourceIngestionService(store, limits)

        e2e_samples = 10
        text_latencies: list[float] = []
        pdf_latencies: list[float] = []
        raw_text_bytes = 0
        stored_text_dir_bytes = 0
        raw_pdf_bytes = 0
        stored_pdf_dir_bytes = 0

        for idx in range(e2e_samples):
            txt_payload = f"{text_sample}\n\n# Unique Sample {idx}\nDetails for benchmark sample {idx}."
            before_bytes = sum(p.stat().st_size for p in tmp_dir.rglob("*") if p.is_file())
            t0 = time.monotonic_ns()
            res = service.register_text_source(
                access=gate,
                repo_factory=SqlSourceRepository,
                bearer=token,
                tenant_id=t_id,
                offering_id=off_id,
                title=f"Bench Text {idx}",
                input_format="typed_notes",
                content=txt_payload,
                origin_kind="teacher_resource",
                visibility="teacher_only",
                original_filename=f"note_{idx}.txt",
                idempotency_key=f"bench-txt-{idx}",
            )
            text_latencies.append((time.monotonic_ns() - t0) / 1_000_000.0)
            after_bytes = sum(p.stat().st_size for p in tmp_dir.rglob("*") if p.is_file())
            if idx == 0:
                raw_text_bytes = res.version.size_bytes
                stored_text_dir_bytes = after_bytes - before_bytes

            # Append unique comment bytes at PDF tail so digest differs cleanly per sample!
            pdf_payload = corpus.multipage_pdf + f"\n% bench-sample-{idx}\n".encode("ascii")
            before_bytes = sum(p.stat().st_size for p in tmp_dir.rglob("*") if p.is_file())
            t0 = time.monotonic_ns()
            pdf_res = service.register_file_source(
                access=gate,
                repo_factory=SqlSourceRepository,
                bearer=token,
                tenant_id=t_id,
                offering_id=off_id,
                title=f"Bench PDF {idx}",
                origin_kind="teacher_resource",
                visibility="offering_published",
                read_bounded_bytes=lambda _m, data=pdf_payload: data,
                declared_format="digital_pdf",
                original_filename=f"doc_{idx}.pdf",
                claimed_content_type="application/pdf",
                idempotency_key=f"bench-pdf-{idx}",
            )
            pdf_latencies.append((time.monotonic_ns() - t0) / 1_000_000.0)
            after_bytes = sum(p.stat().st_size for p in tmp_dir.rglob("*") if p.is_file())
            if idx == 0:
                raw_pdf_bytes = pdf_res.version.size_bytes
                stored_pdf_dir_bytes = after_bytes - before_bytes

        text_latencies.sort()
        pdf_latencies.sort()
        e2e_report["register_text_source_e2e"] = {
            "samples": e2e_samples,
            "raw_input_bytes": raw_text_bytes,
            "stored_artifact_plus_meta_bytes": stored_text_dir_bytes,
            "storage_overhead_bytes": stored_text_dir_bytes - raw_text_bytes,
            "p50_ms": round(percentile(text_latencies, 50), 3),
            "p95_ms": round(percentile(text_latencies, 95), 3),
            "max_ms": round(text_latencies[-1], 3),
        }
        e2e_report["register_multipage_pdf_source_e2e"] = {
            "samples": e2e_samples,
            "pages": 12,
            "raw_input_bytes": raw_pdf_bytes,
            "stored_artifact_plus_meta_bytes": stored_pdf_dir_bytes,
            "storage_overhead_bytes": stored_pdf_dir_bytes - raw_pdf_bytes,
            "p50_ms": round(percentile(pdf_latencies, 50), 3),
            "p95_ms": round(percentile(pdf_latencies, 95), 3),
            "max_ms": round(pdf_latencies[-1], 3),
        }
    finally:
        app_db.close()
        with admin.begin() as conn:
            conn.execute(DropSchema(schema, cascade=True))
            conn.connection.driver_connection.execute(
                sql.SQL("DROP ROLE {}").format(sql.Identifier(role))
            )
        admin.dispose()
        shutil.rmtree(tmp_dir, ignore_errors=True)

    output = {
        "conditions": {
            "platform": os.name,
            "database": "PostgreSQL 17 (localhost viora_test disposable schema, non-owner role + forced RLS)",
            "storage": "LocalFilesystemArtifactStore (payload.bin + meta.json)",
            "limits": limits.model_dump(),
        },
        "microbenchmarks": [item.model_dump() for item in detect_results],
        "end_to_end_registration": e2e_report,
    }
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
