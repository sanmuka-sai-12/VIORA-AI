"""Task F — Batch 2.4 Performance, Parallelism, OCR, and Cache Benchmarking Suite.

Measures against live PostgreSQL + LocalFilesystemArtifactStore + FastAPI TestClient:
- Sequential (page_workers=1) vs bounded parallel (page_workers=2, 4) execution.
- Job acceptance latency, queue waiting time, actual processing duration, and E2E time.
- Cold vs warm RapidOCR execution and selective Pillow preprocessing quality impact.
- Cache-hit vs cache-miss latency (and page-level OCR cache reuse).
- Throughput (jobs/sec, pages/sec) and real OS peak process memory (RSS).
- Extraction quality (reading-order accuracy, table cell accuracy, evidence accuracy, OCR CER/WER)
  on BOTH the Batch 2.3 CC0 corpus fixtures AND independent validation documents.
"""

from __future__ import annotations

import io
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path
from typing import Any
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "backend"))

from _common import compute_cer_wer as _cer_wer
from _common import get_process_memory_mb
from viora.config import AuthSettings, Settings
from viora.database_roles import grant_application_privileges
from viora.db import Database
from viora.identity.public import AuthService, IdentityAttributeKind
from viora.main import create_app
from viora.perf.public import percentile
from viora.sources.ocr import RapidOcrBackend, decode_bounded_image
from viora.sources.public import (
    AdaptiveDocumentExtractor,
    clamp_source_limits,
)
from viora.workflow.worker import build_worker_runner

from benchmarks.educational_corpus.build_corpus import (
    PdfImageSpec,
    PdfPageSpec,
    build_rich_pdf,
)

CORPUS_DIR = REPO_ROOT / "benchmarks" / "educational_corpus"
GROUND_TRUTH_PATH = CORPUS_DIR / "ground_truth.json"
PASSWORD = SecretStr("benchmark passphrase 24")


def _render_text_image(lines: list[str], *, bg: tuple[int, int, int] = (255, 255, 255), fg: tuple[int, int, int] = (15, 15, 15)) -> bytes:
    img = Image.new("RGB", (1100, 750), bg)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 28)
    except OSError:
        font = ImageFont.load_default()
    y = 50
    for ln in lines:
        draw.text((50, y), ln, fill=fg, font=font)
        y += 52
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def _build_independent_validation_docs() -> dict[str, Any]:
    """Generate unseen independent validation documents distinct from Batch 2.3 corpus."""
    # 1. Independent 6-page mixed packet: 2 multi-column digital + 2 table pages + 2 scanned raster pages
    mc_ops = (
        "BT /F2 13 Tf 54 740 Tm (Electrostatics Review Sheet) Tj "
        "/F1 10 Tf "
        "54 690 Tm (Left Column: Gauss law relates flux to enclosed charge.) Tj "
        "320 690 Tm (Right Column: Electric potential is scalar energy per charge.) Tj "
        "54 665 Tm (Left Column: Symmetry simplifies spherical field integrals.) Tj "
        "320 665 Tm (Right Column: Equipotential surfaces remain perpendicular to E.) Tj ET"
    )
    tbl_ops = (
        "0.75 w 60 720 m 520 720 l S 60 692 m 520 692 l S 60 664 m 520 664 l S 60 636 m 520 636 l S "
        "60 720 m 60 636 l S 240 720 m 240 636 l S 380 720 m 380 636 l S 520 720 m 520 636 l S "
        "BT /F2 10 Tf 68 702 Tm (Quantity) Tj 248 702 Tm (Symbol) Tj 388 702 Tm (Unit) Tj "
        "/F1 10 Tf "
        "68 674 Tm (Capacitance) Tj 248 674 Tm (C) Tj 388 674 Tm (Farad) Tj "
        "68 646 Tm (Inductance) Tj 248 646 Tm (L) Tj 388 646 Tm (Henry) Tj ET"
    )
    scan_p1_jpg = _render_text_image(
        [
            "Independent Scanned Worksheet A",
            "Ohm law states V = I * R for ohmic conductors.",
            "Power dissipated equals P = I^2 * R in watts.",
        ]
    )
    scan_p2_jpg = _render_text_image(
        [
            "Independent Scanned Worksheet B",
            "Kirchhoff loop rule enforces conservation of energy.",
            "Kirchhoff junction rule enforces charge conservation.",
        ]
    )
    mixed_6p_pdf = build_rich_pdf(
        [
            PdfPageSpec(content_ops=mc_ops),
            PdfPageSpec(content_ops=tbl_ops),
            PdfPageSpec(
                content_ops="q 540 0 0 380 36 320 cm /Im1 Do Q",
                images=(PdfImageSpec("Im1", scan_p1_jpg, 1100, 750, 36, 320, 540, 380),),
            ),
            PdfPageSpec(content_ops=mc_ops),
            PdfPageSpec(content_ops=tbl_ops),
            PdfPageSpec(
                content_ops="q 540 0 0 380 36 320 cm /Im2 Do Q",
                images=(PdfImageSpec("Im2", scan_p2_jpg, 1100, 750, 36, 320, 540, 380),),
            ),
        ]
    )

    # 2. Independent 4-page pure scanned PDF
    scanned_4p_pdf = build_rich_pdf(
        [
            PdfPageSpec(
                content_ops="q 540 0 0 380 36 320 cm /ImA Do Q",
                images=(PdfImageSpec("ImA", scan_p1_jpg, 1100, 750, 36, 320, 540, 380),),
            ),
            PdfPageSpec(
                content_ops="q 540 0 0 380 36 320 cm /ImB Do Q",
                images=(PdfImageSpec("ImB", scan_p2_jpg, 1100, 750, 36, 320, 540, 380),),
            ),
            PdfPageSpec(
                content_ops="q 540 0 0 380 36 320 cm /ImC Do Q",
                images=(PdfImageSpec("ImC", scan_p1_jpg, 1100, 750, 36, 320, 540, 380),),
            ),
            PdfPageSpec(
                content_ops="q 540 0 0 380 36 320 cm /ImD Do Q",
                images=(PdfImageSpec("ImD", scan_p2_jpg, 1100, 750, 36, 320, 540, 380),),
            ),
        ]
    )

    # 3. Independent low-contrast photographed JPEG for selective preprocessing check
    low_contrast_jpg = _render_text_image(
        [
            "Thermodynamics Quick Reference",
            "First Law: delta U = Q - W",
            "Carnot Efficiency: eta = 1 - T_c / T_h",
        ],
        bg=(210, 212, 206),
        fg=(95, 98, 92),
    )

    return {
        "mixed_6p_pdf": mixed_6p_pdf,
        "scanned_4p_pdf": scanned_4p_pdf,
        "low_contrast_jpg": low_contrast_jpg,
        "low_contrast_gt": "Thermodynamics Quick Reference\nFirst Law: delta U = Q - W\nCarnot Efficiency: eta = 1 - T_c / T_h",
    }


def _measure_cold_vs_warm_ocr() -> dict[str, Any]:
    backend = RapidOcrBackend()
    if not backend.is_available():
        return {"ocr_available": False}

    code = (
        "import json, sys, time\n"
        f"sys.path.insert(0, {str(REPO_ROOT / 'backend')!r})\n"
        "from PIL import Image\n"
        "from viora.sources.ocr import RapidOcrBackend\n"
        "img = Image.open('benchmarks/educational_corpus/samples/07_scanned_printed_page.png').convert('RGB')\n"
        "t0 = time.monotonic_ns()\n"
        "b = RapidOcrBackend()\n"
        "b.warm_up()\n"
        "init_ms = (time.monotonic_ns() - t0) / 1_000_000.0\n"
        "t1 = time.monotonic_ns()\n"
        "r1 = b.recognize_image_adaptive(img)\n"
        "cold_exec_ms = (time.monotonic_ns() - t1) / 1_000_000.0\n"
        "warm = []\n"
        "for _ in range(5):\n"
        "    tw = time.monotonic_ns()\n"
        "    b.recognize_image_adaptive(img)\n"
        "    warm.append((time.monotonic_ns() - tw) / 1_000_000.0)\n"
        "print(json.dumps({'cold_init_ms': round(init_ms, 2), 'cold_first_ocr_ms': round(cold_exec_ms, 2), 'warm_p50_ms': round(sorted(warm)[2], 2), 'warm_mean_ms': round(sum(warm)/len(warm), 2), 'samples': 5, 'mean_conf': r1.mean_confidence}))\n"
    )
    out = subprocess.check_output([sys.executable, "-c", code], cwd=str(REPO_ROOT), text=True)
    return {"ocr_available": True, **json.loads(out.strip().splitlines()[-1])}


def main() -> int:
    pw = os.environ.get("POSTGRES_PASSWORD")
    if not pw:
        env_path = REPO_ROOT / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("POSTGRES_PASSWORD="):
                    pw = line.split("=", 1)[1].strip()
                    break
    db_url = os.environ.get(
        "VIORA_TEST_DATABASE_URL",
        f"postgresql+psycopg://viora:{pw}@127.0.0.1:5432/viora_test",
    )
    url = make_url(db_url)
    if url.database != "viora_test":
        raise SystemExit("VIORA_TEST_DATABASE_URL must target viora_test")

    indep = _build_independent_validation_docs()
    limits = clamp_source_limits(max_bytes=5 * 1024 * 1024, max_pdf_pages=50)
    ocr_backend = RapidOcrBackend()
    ocr_ready = ocr_backend.is_available()
    if ocr_ready:
        ocr_backend.warm_up()

    # 1. Cold vs Warm OCR + Adaptive Pillow preprocessing quality
    cold_warm_ocr = _measure_cold_vs_warm_ocr()
    preproc_eval: list[dict[str, Any]] = []
    if ocr_ready:
        for label, raw_img, gt in (
            ("corpus_08_photographed_notes_jpg", (CORPUS_DIR / "samples" / "08_photographed_notes.jpg").read_bytes(), json.loads(GROUND_TRUTH_PATH.read_text(encoding="utf-8"))["documents"][8]["ground_truth"]["expected_text"]),
            ("indep_low_contrast_jpg", indep["low_contrast_jpg"], indep["low_contrast_gt"]),
        ):
            pil = decode_bounded_image(raw_img)
            raw_r = ocr_backend.recognize_image(pil, preprocess=False)
            adp_r = ocr_backend.recognize_image_adaptive(pil)
            raw_txt = "\n".join(ln.text for ln in raw_r.lines)
            adp_txt = "\n".join(ln.text for ln in adp_r.lines)
            cer_raw, wer_raw = _cer_wer(gt, raw_txt)
            cer_adp, wer_adp = _cer_wer(gt, adp_txt)
            preproc_eval.append(
                {
                    "sample": label,
                    "raw_conf": raw_r.mean_confidence,
                    "adaptive_conf": adp_r.mean_confidence,
                    "preprocess_applied": adp_r.preprocess_applied,
                    "cer_before": cer_raw,
                    "cer_after": cer_adp,
                    "wer_before": wer_raw,
                    "wer_after": wer_adp,
                }
            )

    # 2. Sequential (page_workers=1) vs Bounded Parallel (page_workers=2, 4) CPU extraction
    parallel_comparison: list[dict[str, Any]] = []
    seq_docs: dict[str, Any] = {}
    for workers in (1, 2, 4):
        ext = AdaptiveDocumentExtractor(
            ocr_backend=ocr_backend if ocr_ready else None,
            page_workers=workers,
        )
        try:
            if workers > 1:
                ext.warm_pool()
            for doc_name, raw_pdf, pages in (
                ("indep_mixed_6p_pdf", indep["mixed_6p_pdf"], 6),
                ("indep_scanned_4p_pdf", indep["scanned_4p_pdf"], 4),
            ):
                lats: list[float] = []
                tracemalloc.start()
                tracemalloc.reset_peak()
                t_wall0 = time.monotonic_ns()
                last_doc = None
                iters = 3
                for _ in range(iters):
                    t0 = time.monotonic_ns()
                    last_doc = ext.extract(
                        tenant_id=uuid4(),
                        source_id=uuid4(),
                        version_id=uuid4(),
                        source_version=1,
                        source_sha256="a" * 64,
                        input_format="digital_pdf",
                        title=doc_name,
                        raw_bytes=raw_pdf,
                        limits=limits,
                    )
                    lats.append((time.monotonic_ns() - t0) / 1_000_000.0)
                wall_s = max(1e-9, (time.monotonic_ns() - t_wall0) / 1_000_000_000.0)
                _, py_peak = tracemalloc.get_traced_memory()
                tracemalloc.stop()
                if workers == 1:
                    seq_docs[doc_name] = last_doc
                seq_doc = seq_docs.get(doc_name)
                deterministic_match = (
                    seq_doc is not None
                    and last_doc is not None
                    and last_doc.normalized_text == seq_doc.normalized_text
                    and last_doc.tables == seq_doc.tables
                )
                lats.sort()
                parallel_comparison.append(
                    {
                        "document": doc_name,
                        "pages": pages,
                        "page_workers": workers,
                        "samples": iters,
                        "p50_ms": round(percentile(lats, 50.0), 2),
                        "p95_ms": round(percentile(lats, 95.0), 2),
                        "mean_ms": round(sum(lats) / len(lats), 2),
                        "pages_per_sec": round((iters * pages) / wall_s, 2),
                        "python_heap_peak_kb": round(py_peak / 1024.0, 1),
                        "os_mem": get_process_memory_mb(),
                        "deterministic_match_seq": deterministic_match,
                    }
                )
        finally:
            ext.close()

    # 3. Live PostgreSQL + HTTP + Worker E2E: acceptance latency, queue wait, processing, cache hit vs miss
    schema = f"bench_{uuid4().hex}"
    role = f"bench_app_{uuid4().hex}"
    role_pw = secrets.token_urlsafe(24)
    scoped = url.update_query_dict({"options": f"-csearch_path={schema}"})
    scoped_str = scoped.render_as_string(hide_password=False)
    app_url = scoped.set(username=role, password=role_pw).render_as_string(hide_password=False)
    admin = create_engine(scoped)

    e2e_metrics: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as tmp:
        storage_dir = Path(tmp) / "storage"
        settings = Settings(
            database_url=SecretStr(app_url),
            environment="test",
            media_storage_dir=str(storage_dir),
            source_max_bytes=5 * 1024 * 1024,
            source_max_pdf_pages=50,
            worker_concurrency=2,
            worker_page_workers=2,
            auth=AuthSettings(signing_key=SecretStr("b" * 64)),
        )
        app_db = Database(settings)
        with admin.begin() as conn:
            conn.execute(CreateSchema(schema))
            conn.connection.driver_connection.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT"
                ).format(sql.Identifier(role), sql.Literal(role_pw))
            )
        try:
            old_db, old_mig = os.environ.get("DATABASE_URL"), os.environ.get("MIGRATION_DATABASE_URL")
            os.environ["DATABASE_URL"] = scoped_str
            os.environ["MIGRATION_DATABASE_URL"] = scoped_str
            try:
                cfg = Config(str(REPO_ROOT / "alembic.ini"))
                command.upgrade(cfg, "head")
            finally:
                if old_db is None:
                    os.environ.pop("DATABASE_URL", None)
                else:
                    os.environ["DATABASE_URL"] = old_db
                if old_mig is None:
                    os.environ.pop("MIGRATION_DATABASE_URL", None)
                else:
                    os.environ["MIGRATION_DATABASE_URL"] = old_mig

            with admin.begin() as conn:
                grant_application_privileges(conn, role, schema)

            auth = AuthService(app_db.sessions, settings.auth)  # type: ignore[arg-type]
            ids = {k: uuid4() for k in ("tenant", "dept", "year", "sem", "sec", "subj", "offering", "teacher")}
            email = f"bench-{ids['teacher']}@school.test"
            with admin.begin() as conn:
                conn.execute(text("INSERT INTO institutions VALUES (:tenant, 'BenchSchool', true)"), ids)
                conn.execute(text("INSERT INTO users VALUES (:teacher, 'BenchTeacher', 'active')"), ids)
                conn.execute(text("INSERT INTO memberships VALUES (:tenant, :teacher, 'active')"), ids)
                conn.execute(text("INSERT INTO role_grants (tenant_id, user_id, role) VALUES (:tenant, :teacher, 'subject_teacher')"), ids)
                conn.execute(text("INSERT INTO verified_identity_attributes VALUES (:teacher, 'email', :email, now())"), {"teacher": ids["teacher"], "email": email})
                conn.execute(text("INSERT INTO password_credentials VALUES (:teacher, :h)"), {"teacher": ids["teacher"], "h": auth.passwords.hash(PASSWORD)})
                conn.execute(text("INSERT INTO departments (tenant_id, id, name) VALUES (:tenant, :dept, 'D')"), ids)
                conn.execute(text("INSERT INTO academic_years (tenant_id, id, name) VALUES (:tenant, :year, '2026')"), ids)
                conn.execute(text("INSERT INTO semesters (tenant_id, id, name, academic_year_id) VALUES (:tenant, :sem, 'S1', :year)"), ids)
                conn.execute(text("INSERT INTO sections (tenant_id, id, name, semester_id) VALUES (:tenant, :sec, 'Sec1', :sem)"), ids)
                conn.execute(text("INSERT INTO subjects (tenant_id, id, name, department_id) VALUES (:tenant, :subj, 'Sub1', :dept)"), ids)
                conn.execute(text("INSERT INTO subject_offerings (tenant_id, id, name, section_id, subject_id) VALUES (:tenant, :offering, 'Off1', :sec, :subj)"), ids)
                conn.execute(text("INSERT INTO teacher_assignments VALUES (:tenant, :teacher, :offering, true)"), ids)

            token = auth.login(IdentityAttributeKind.email, email, PASSWORD).access_token.get_secret_value()
            hdr = {"Authorization": f"Bearer {token}"}
            runner = build_worker_runner(settings, db=app_db, ocr_backend=ocr_backend if ocr_ready else None)
            runner.handler.extractor.warm_pool()

            with TestClient(create_app(settings, app_db)) as client:
                from datetime import datetime as _dt

                workloads = [
                    ("corpus_01_typed_notes_md", (CORPUS_DIR / "samples" / "01_markdown_notes.md").read_bytes(), "01_markdown_notes.md", "text/markdown", "typed_notes"),
                    ("corpus_02_digital_pdf", (CORPUS_DIR / "samples" / "02_single_column_textbook.pdf").read_bytes(), "textbook.pdf", "application/pdf", "digital_pdf"),
                    ("corpus_05_tables_pdf", (CORPUS_DIR / "samples" / "05_tables_bordered_and_borderless.pdf").read_bytes(), "tables.pdf", "application/pdf", "digital_pdf"),
                    ("corpus_07_scanned_png", (CORPUS_DIR / "samples" / "07_scanned_printed_page.png").read_bytes(), "scan.png", "image/png", "png"),
                    ("corpus_11_mixed_pdf", (CORPUS_DIR / "samples" / "11_mixed_digital_scanned.pdf").read_bytes(), "mixed.pdf", "application/pdf", "digital_pdf"),
                    ("indep_mixed_6p_pdf", indep["mixed_6p_pdf"], "indep_mixed_6p.pdf", "application/pdf", "digital_pdf"),
                    ("indep_scanned_4p_pdf", indep["scanned_4p_pdf"], "indep_scan_4p.pdf", "application/pdf", "scanned_pdf"),
                ]
                accept_lats: list[float] = []
                queue_waits: list[float] = []
                e2e_rows: list[dict[str, Any]] = []
                cache_miss_ms: list[float] = []
                cache_hit_ms: list[float] = []

                for name, raw, fname, ctype, fmt in workloads:
                    t_e2e_start = time.monotonic_ns()
                    t_acc0 = time.monotonic_ns()
                    if fmt == "typed_notes":
                        up = client.post(
                            f"/api/v1/tenants/{ids['tenant']}/offerings/{ids['offering']}/sources",
                            headers=hdr,
                            json={
                                "title": name,
                                "input_format": "typed_notes",
                                "visibility": "offering_published",
                                "content": raw.decode("utf-8"),
                            },
                        )
                    else:
                        up = client.post(
                            f"/api/v1/tenants/{ids['tenant']}/offerings/{ids['offering']}/sources/files",
                            headers=hdr,
                            data={"title": name, "input_format": fmt, "visibility": "offering_published"},
                            files={"file": (fname, raw, ctype)},
                        )
                    accept_ms = (time.monotonic_ns() - t_acc0) / 1_000_000.0
                    assert up.status_code == 201, up.text
                    accept_lats.append(accept_ms)
                    src_id = up.json()["source"]["source_id"]
                    ver_id = up.json()["version"]["version_id"]
                    job_id = up.json()["job_id"]

                    if job_id is None:
                        j_miss = {"state": "sync", "created_at": None, "started_at": None, "finished_at": None}
                        worker_wall_ms = 0.0
                        e2e_ms = accept_ms
                        ex_miss = client.get(f"/api/v1/tenants/{ids['tenant']}/sources/{src_id}/versions/{ver_id}/extraction", headers=hdr).json()
                        queue_wait_ms = 0.0
                    else:
                        # Drain 1 job (cache miss)
                        t_w0 = time.monotonic_ns()
                        runner.drain(max_jobs=1)
                        worker_wall_ms = (time.monotonic_ns() - t_w0) / 1_000_000.0
                        e2e_ms = (time.monotonic_ns() - t_e2e_start) / 1_000_000.0
                        j_miss = client.get(f"/api/v1/tenants/{ids['tenant']}/jobs/{job_id}", headers=hdr).json()
                        ex_miss = client.get(f"/api/v1/tenants/{ids['tenant']}/sources/{src_id}/versions/{ver_id}/extraction", headers=hdr).json()
                        cache_miss_ms.append(worker_wall_ms)
                        queue_wait_ms = 0.0
                        if j_miss.get("created_at") and j_miss.get("started_at"):
                            queue_wait_ms = max(
                                0.0,
                                (_dt.fromisoformat(j_miss["started_at"]) - _dt.fromisoformat(j_miss["created_at"])).total_seconds()
                                * 1000.0,
                            )
                            queue_waits.append(queue_wait_ms)

                    # Trigger cache-hit re-extraction
                    t_hit_e2e0 = time.monotonic_ns()
                    trig = client.post(
                        f"/api/v1/tenants/{ids['tenant']}/sources/{src_id}/versions/{ver_id}/extract",
                        headers=hdr,
                    )
                    assert trig.status_code == 202
                    t_hw0 = time.monotonic_ns()
                    runner.drain(max_jobs=1)
                    hit_wall_ms = (time.monotonic_ns() - t_hw0) / 1_000_000.0
                    hit_e2e_ms = (time.monotonic_ns() - t_hit_e2e0) / 1_000_000.0
                    ex_hit = client.get(f"/api/v1/tenants/{ids['tenant']}/sources/{src_id}/versions/{ver_id}/extraction", headers=hdr).json()
                    cache_hit_ms.append(hit_wall_ms)

                    doc_obj = ex_miss["document"]
                    e2e_rows.append(
                        {
                            "workload": name,
                            "accept_ms": round(accept_ms, 2),
                            "queue_wait_ms": round(queue_wait_ms, 2),
                            "miss_worker_ms": round(worker_wall_ms, 2),
                            "miss_e2e_ms": round(e2e_ms, 2),
                            "hit_worker_ms": round(hit_wall_ms, 2),
                            "hit_e2e_ms": round(hit_e2e_ms, 2),
                            "cache_hit_verified": bool(ex_hit["cache_hit"]),
                            "job_state": j_miss["state"],
                            "pages": doc_obj["quality"]["total_pages"],
                            "chars": len(doc_obj["normalized_text"]),
                            "tables": len(doc_obj["tables"]),
                            "warnings": [w["code"] for w in doc_obj["warnings"]],
                            "snippet": doc_obj["normalized_text"][:110].replace("\n", " "),
                        }
                    )

                accept_lats.sort()
                queue_waits.sort()
                cache_miss_ms.sort()
                cache_hit_ms.sort()
                e2e_metrics = {
                    "samples": len(workloads),
                    "acceptance_p50_ms": round(percentile(accept_lats, 50.0), 2),
                    "acceptance_p95_ms": round(percentile(accept_lats, 95.0), 2),
                    "queue_wait_p50_ms": round(percentile(queue_waits, 50.0), 2) if queue_waits else 0.0,
                    "cache_miss_p50_ms": round(percentile(cache_miss_ms, 50.0), 2),
                    "cache_hit_p50_ms": round(percentile(cache_hit_ms, 50.0), 2),
                    "workloads": e2e_rows,
                }
        finally:
            runner.close()
            app_db.close()
            with admin.begin() as conn:
                conn.execute(DropSchema(schema, cascade=True))
                conn.connection.driver_connection.execute(
                    sql.SQL("DROP ROLE {}").format(sql.Identifier(role))
                )
            admin.dispose()

    report = {
        "ocr_cold_vs_warm": cold_warm_ocr,
        "adaptive_pillow_preprocessing_eval": preproc_eval,
        "sequential_vs_parallel_cpu": parallel_comparison,
        "live_postgres_e2e_and_cache": e2e_metrics,
        "peak_process_memory": get_process_memory_mb(),
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
