# arxiv-pipeline

Shared source code for an arXiv project: LaTeX collection, parsing, and reference matching. Introduction to Data Science.

Milestone 1 (this package, `src/crawler/`): for each assigned arXiv id, collect **every version**
(only `.tex`/`.bib`), paper metadata, and references from Semantic Scholar. Data is never committed.

## Setup

Python 3.11 (3.10+ should work; Colab's default is fine).

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate      Linux/macOS: source .venv/bin/activate
pip install -r src/requirements-dev.txt
pre-commit install
pytest
```

Environment variables (never commit them; copy nothing into the repo):

| Variable           | Meaning                                                            |
| ------------------ | ------------------------------------------------------------------ |
| `CRAWLER_CONTACT`  | e-mail/URL shown in the User-Agent (required for real crawling)    |
| `S2_API_KEY`       | Semantic Scholar key (on Colab: use Colab Secrets)                 |
| `CRAWLER_DATA_DIR` | output directory, default `./data` (git-ignored)                   |

## Run

Each member crawls only their own ranges. A range file is a CSV
`start_month,start_id,end_month,end_id` (both ends inclusive); see `src/id_ranges.example.csv`.

```bash
python src/run_crawl.py --ranges src/my_ranges.csv --data-dir data
python src/run_crawl.py --ranges src/my_ranges.csv --limit 20     # smoke test
python src/validate_corpus.py --data-dir data
```

On Colab, use `src/colab_run.ipynb`.

> Project status: see [`STATUS.md`](STATUS.md) (read section 0 for a quick overview).
> Scaffold only so far: modules raise `NotImplementedError("T…")` until their owner implements them.

## Layout

```
src/
  crawler/
    models.py      # data contract shared by all modules (draft, T0.1)
    config.py      # settings from env vars
    http.py        # Session + per-host RateLimiter + retry          (A, T1.1)
    manifest.py    # SQLite state, resume                            (A, T1.2)
    ids.py         # id normalising, range expansion, dirnames       (B, T1.3)
    discovery.py   # arXiv API: metadata + number of versions        (B, T2.1)
    source.py      # download /src/, safe .tex/.bib extraction       (C, T2.2)
    s2.py          # Semantic Scholar references                     (D, T2.3)
    metrics.py     # wall time, RAM, disk                            (A, T2.4)
    pipeline.py    # two workers, resume, atomic writes              (A, T3.1)
    export.py      # papers/references tables + stats                (D, T3.2/T4.4)
  corpus.py            # read API for Milestones 2/3                 (D+E, T3.2)
  validate_corpus.py   # post-crawl invariants                       (E, T3.2)
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
