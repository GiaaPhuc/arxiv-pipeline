# Instructions for AI agents

## Start here

1. Read `STATUS.md` section 0 ("Đọc nhanh") first. It already compares the assignment in
   `project requirements/` with what the repo has now (phase, gaps, blockers, next step).
   Do not re-read the requirement files on every prompt. Open them only when you need the
   original wording, a code sample from the brief, or a grading detail that section 0 does not
   capture. Read the rest of `STATUS.md` when you need a decision, a task row, or a risk.
2. The module you are asked to touch has a docstring at the top with its owner, reviewer,
   task code and "done when" condition. Follow it.
3. `src/crawler/models.py` is the shared data contract. Do not change it as a side effect of
   another task; propose the change explicitly.

## Keep `STATUS.md` current

After finishing any piece of work in this repo, update `STATUS.md` in the same change:

- set "Cập nhật lần cuối" to the current date/time;
- update the Milestone 1 requirement table in section 0 when a requirement moves (stub →
  partial → done), plus the "Việc đang làm" bullets and the affected rows in section 2
  (verify numbers by running `pytest` / `git status` instead of guessing);
- add a new row at the TOP of the changelog (section 4);
- adjust decisions (1) and risks (3) if they changed.

Write in Vietnamese, short and factual. Never mark a task ✅ unless its "done when" condition is met.

## Rules

- Unit tests must not hit the real network (use `responses` + synthetic fixtures).
- Never commit `data/`, `.env`, API keys, `*.gz`, `*.parquet`, `*.sqlite`.
- arXiv only via `export.arxiv.org`, >= 3 s between requests, one connection; Semantic Scholar >= 1 s.
- Before finishing: `ruff check . && ruff format . && pytest`.
- Do not edit another member's module on their branch or commit on their behalf.
