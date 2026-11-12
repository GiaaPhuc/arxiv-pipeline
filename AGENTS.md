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

## Coding behavior

Guidelines to reduce common LLM coding mistakes. They bias toward caution over speed; for
trivial tasks, use judgment.

### 1. Think before coding

Don't assume. Don't hide confusion. Surface tradeoffs.

- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them; don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop, name what is confusing, and ask.

### 2. Simplicity first

Minimum code that solves the problem. Nothing speculative.

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that was not requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical changes

Touch only what you must. Clean up only your own mess.

- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you would do it differently.
- If you notice unrelated dead code, mention it; don't delete it.
- Remove imports/variables/functions that YOUR changes made unused; leave pre-existing dead
  code unless asked.

Test: every changed line should trace directly to the user's request.

### 4. Goal-driven execution

Define success criteria. Loop until verified.

- "Add validation" → write tests for invalid inputs, then make them pass.
- "Fix the bug" → write a test that reproduces it, then make it pass.
- "Refactor X" → ensure tests pass before and after.

For multi-step tasks, state a brief plan:

```text
1. [Step] → verify: [check]
2. [Step] → verify: [check]
```

Strong success criteria let you loop independently; weak ones ("make it work") need constant
clarification.

## Keep `STATUS.md` current

After finishing any piece of work in this repo, update `STATUS.md` in the same change:

- set "Cập nhật lần cuối" to the current date/time;
- update the Milestone 1 requirement table in section 0 when a requirement moves (stub →
  partial → done), plus the "Việc đang làm" bullets and the affected rows in section 2
  (verify numbers by running `pytest` / `git status` instead of guessing);
- add a new row at the TOP of section 4 only when the work changes a fact the Milestone 1
  report must state: how data is collected, which tools are used, a measured number, or a
  limitation met while collecting. Do not log meetings, role assignments, internal schedules,
  or edits to working files;
- adjust decisions (1) and risks (3) if they changed.
- if the change alters the processing flow (threads, queues, rate limiting, extraction, S2,
  resume/checkpoint, data layout), also update `docs/du-an-va-luong-xu-ly-hien-tai-vi.md` in
  the same change so it stays accurate with the code.

Write in Vietnamese, short and factual. Never mark a task ✅ unless its "done when" condition is met.

## Rules

- Unit tests must not hit the real network (use `responses` + synthetic fixtures).
- Never commit `data/`, `.env`, API keys, `*.gz`, `*.parquet`, `*.sqlite`.
- arXiv only via `export.arxiv.org`, >= 3 s between requests, one connection; Semantic Scholar >= 1 s.
- Before finishing: `ruff check . && ruff format . && pytest`.
- Do not edit another member's module on their branch or commit on their behalf.
