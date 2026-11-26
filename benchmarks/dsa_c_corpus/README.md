# VIORA-DSA Academic C Corpus (Pilot)

Originally authored open-licensed (**CC0-1.0**) research corpus for **Data Structures and Algorithms in C (C99)**.
Designed strictly as a versioned academic fixture and Phase 2 document-processing evaluation suite (not production database data, not LLM fine-tuning data, and not Phase 3 retrieval code).

## Structure

- `corpus_concepts.json`: 16 canonical concept records spanning all 7 core C/DSA topic areas:
  1. Arrays, pointers, and structures
  2. Time and space complexity (asymptotic & amortized analysis)
  3. Stacks and queues
  4. Recursion and recurrences
  5. Singly, doubly, and circular linked lists
  6. Trees, heaps, and graphs
  7. Searching, sorting, and hashing
- `c_programs/`: 7 standalone C99 test harnesses with executable `assert` suites verifying memory invariants, wrap-around indices, pointer manipulation, sorting/searching bounds, BST/min-heap ordering, BFS, tombstone hash probing, and a safe bounds-checked BFS duplicate-insertion counterexample (`07_bfs_duplicate_counterexample.c`).
- `eval_questions.json`: 24 independent held-out evaluation questions across 8 rubric categories (definitions, conceptual reasoning, complexity analysis, code implementation, debugging, algorithm selection, edge cases, and ambiguous/unanswerable questions). Questions are paraphrased rather than copied verbatim from concept text.
- `experiment_dsa_ai.py` & `ai_experiment_results.json`: Bounded 3-request Cloudflare Worker experimental harness (dry-run/preflight by default, no adapter duplication) and sanitized captured outputs/telemetry.
- `build_dsa_fixtures.py` & `fixtures/`: Representative Phase 2 document fixtures derived cleanly from the C/DSA domain (Markdown notes, native C code PDF, two-column C complexity notes PDF, bordered/borderless complexity table PDF, recurrence/math notation PDF, scanned raster PNG, photographed note JPEG).

## Reproducing C Program Verification

Compile and execute each C program into a temporary directory using GCC C99 strict warnings:

```powershell
mkdir $env:TEMP\viora_dsa_bin -Force | Out-Null
Get-ChildItem benchmarks\dsa_c_corpus\c_programs\*.c | ForEach-Object {
  $exe = Join-Path "$env:TEMP\viora_dsa_bin" ($_.BaseName + ".exe")
  gcc -std=c99 -Wall -Wextra -Werror $_.FullName -o $exe; if ($LASTEXITCODE -ne 0) { exit 1 }
  & $exe; if ($LASTEXITCODE -ne 0) { exit 1 }
}
```
