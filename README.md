# arxiv-pipeline

Shared source code for an arXiv project: LaTeX collection, parsing, and reference matching. Introduction to Data Science.

Milestone 1 (this package, `src/crawler/`): for each assigned arXiv id, collect **every version**
(only `.tex`/`.bib`), paper metadata, and references from Semantic Scholar. Data is never committed.

Libraries: `requests` (HTTP, one rate limiter per host), `psutil` (RAM sampling), `pyarrow`
(Parquet tables), `pandas` (notebooks); standard library for `tarfile`, `gzip`, `sqlite3`.

## Setup

Python 3.11 (3.10+ should work; Colab's default is fine).

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate      Linux/macOS: source .venv/bin/activate
pip install -r src/requirements-dev.txt
pre-commit install
pytest
```

**Chỉ lint + test (nhanh, ~3 giây):** cài `src/requirements-ci.txt` (không pandas/pyarrow), rồi `.\scripts\check.ps1`.
CI GitHub cũng dùng file này — pytest thực tế ~1 giây; nếu UI “chờ” 5–10 phút thường là **hàng đợi runner** hoặc **`pip install` lần đầu** trên máy, không phải test treo.

Environment variables (never commit them; copy nothing into the repo):

| Variable           | Meaning                                                            |
| ------------------ | ------------------------------------------------------------------ |
| `CRAWLER_CONTACT`  | e-mail/URL shown in the User-Agent (required for real crawling)    |
| `S2_API_KEY`       | Semantic Scholar key (on Colab: use Colab Secrets)                 |
| `CRAWLER_DATA_DIR` | output directory, default `./data` (git-ignored)                   |

## Run

The whole assigned set (`src/id_ranges.csv`, 25,000 ids) is crawled once, on ONE machine.
A range file is a CSV `start_month,start_id,end_month,end_id` (both ends inclusive); see
`src/id_ranges.example.csv`. Set `CRAWLER_CONTACT` first (e-mail or URL for the User-Agent;
the crawler refuses to start without it) and `S2_API_KEY` if you have one.

```bash
python src/run_crawl.py --ranges src/id_ranges.csv --dry-run            # plan only, no network
python src/run_crawl.py --ranges src/id_ranges.csv --limit 30            # pilot
python src/run_crawl.py --ranges src/id_ranges.csv --data-dir data       # full crawl
python src/validate_corpus.py --data-dir data                            # invariants, exit 0 = PASS
python src/export_data.py --data-dir data                                # tables + stats -> data/stats/
```

**Resume.** Stop with Ctrl+C (or lose the machine) and run the same command again on the same
`data/`: pairs in a final state are skipped, `failed` pairs are retried, papers whose Semantic
Scholar step did not finish are queued again. Never run two crawlers on one data directory.

**Exit codes of `run_crawl.py`:** `0` finished, `2` bad setup, `3` arXiv answered 403/429
(stopped on purpose: tell the team, do not speed up), `130` interrupted.

**Output** (`data/`, never committed): `papers/<id>/paper.json`, `v<N>/meta.json`,
`v<N>/references.json`, `v<N>/tex/` (only `.tex`/`.bib`), `s2_references.raw.json.gz`,
`manifest.sqlite`, `logs/crawl.log`, `logs/metrics.json` (cumulative wall time per stage, peak
and mean RAM, peak disk, summed over resumed runs).

On Colab, use `src/colab_run.ipynb`.

> Project status: see [`STATUS.md`](STATUS.md) (read section 0 for a quick overview).

## Layout

```
src/
  crawler/
    models.py      # data contract shared by all modules (draft, T0.1)
    config.py      # settings from env vars
    http.py        # Session + per-host RateLimiter + retry          (A, T1.1)
    manifest.py    # SQLite state, resume                            (A, T1.2)
    ids.py         # id normalising, range expansion, dirnames       (B, T1.3)
    discovery.py   # arXiv Atom API: metadata, N versions, version dates (B, T2.1)
    source.py      # download /src/, safe .tex/.bib extraction       (C, T2.2)
    s2.py          # Semantic Scholar references + venue fallback    (B, T2.3)
    metrics.py     # wall time, RAM, disk                            (A, T2.4)
    pipeline.py    # two workers, resume, atomic writes              (A, T3.1)
    export.py      # papers/references tables + stats                (D, T3.2/T4.4)
  corpus.py            # read API for Milestones 2/3                 (D+E, T3.2)
  validate_corpus.py   # post-crawl invariants                       (E, T3.2)
  export_data.py       # CLI: tables + report statistics             (D, T4.4)
  run_crawl.py         # CLI entry point
  colab_run.ipynb      # Colab runner
  tests/               # pytest, no real network
```

Every module's docstring states its owner, reviewer, task code and "done when" condition.

## Working agreement

Full rules: [`CONTRIBUTING.md`](CONTRIBUTING.md).

- One task = one owner. Branch name: `m1/<role>-<short-description>` (e.g. `m1/c-source-extract`).
  Never push directly to `main`; open a PR, one reviewer who did **not** write the module.
- Commit with your own account.
- Unit tests must not touch the real network (use `responses` and synthetic fixtures).
- Run `ruff check . && ruff format . && pytest` before opening a PR (pre-commit does the lint part).
- Never commit `data/`, `.env`, API keys, `*.gz`, `*.parquet`, `*.sqlite`.
- Be polite to the servers: arXiv only via `export.arxiv.org`, >= 3 s between requests, one connection;
  Semantic Scholar >= 1 s. On 403/429: stop and tell the team, do not speed up.
