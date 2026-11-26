"""Shared memory, OCR metric, and database helper utilities for VIORA benchmark scripts."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.wintypes.DWORD),
        ("PageFaultCount", ctypes.wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def get_process_memory_mb() -> dict[str, float]:
    """Read real OS process working set & peak working set separately from Python heap."""
    if os.name == "nt":
        pmc = _PROCESS_MEMORY_COUNTERS()
        pmc.cb = ctypes.sizeof(pmc)
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = ctypes.wintypes.HANDLE
        k32.K32GetProcessMemoryInfo.argtypes = [
            ctypes.wintypes.HANDLE,
            ctypes.POINTER(_PROCESS_MEMORY_COUNTERS),
            ctypes.wintypes.DWORD,
        ]
        k32.K32GetProcessMemoryInfo.restype = ctypes.wintypes.BOOL
        if k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return {
                "rss_mb": round(pmc.WorkingSetSize / (1024 * 1024), 2),
                "peak_rss_mb": round(pmc.PeakWorkingSetSize / (1024 * 1024), 2),
            }
    return {"rss_mb": 0.0, "peak_rss_mb": 0.0}


def levenshtein_distance(seq_a: list[str] | str, seq_b: list[str] | str) -> int:
    n, m = len(seq_a), len(seq_b)
    if n == 0:
        return m
    if m == 0:
        return n
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        ai = seq_a[i - 1]
        for j in range(1, m + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (0 if ai == b_at(seq_b, j - 1) else 1))
        prev = cur
    return prev[m]


def b_at(seq: list[str] | str, idx: int) -> str:
    return seq[idx]


def compute_cer_wer(reference: str, hypothesis: str) -> tuple[float, float]:
    ref_clean = " ".join(reference.split())
    hyp_clean = " ".join(hypothesis.split())
    cer = levenshtein_distance(ref_clean, hyp_clean) / max(1, len(ref_clean))
    wer = levenshtein_distance(ref_clean.split(), hyp_clean.split()) / max(
        1, len(ref_clean.split())
    )
    return round(cer, 4), round(wer, 4)


def load_postgres_test_url(repo_root: Path = REPO_ROOT) -> str:
    pw = ""
    env_file = repo_root / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("POSTGRES_PASSWORD="):
                pw = line.split("=", 1)[1].strip()
    raw_url = os.environ.get("VIORA_TEST_DATABASE_URL") or (
        f"postgresql+psycopg://viora:{pw}@127.0.0.1:5432/viora_test" if pw else ""
    )
    if not raw_url:
        raise SystemExit("VIORA_TEST_DATABASE_URL or .env POSTGRES_PASSWORD required")
    return raw_url
