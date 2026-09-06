# Five-Minute Interview Script

One failed run and one A/B result — that is the whole story. Everything
below runs offline and can be demonstrated live. Keep the limits section for
the end; volunteering limitations is part of the pitch.

## 0:00–1:00 — The question AgentFlow answers

> "Agents fail or behave strangely, and the debugging surface is a print
> statement. AgentFlow asks one question: **why did the agent behave this
> way?** — and answers it with recorded artifacts instead of guesses: the
> exact prompt, the exact context window per step, every tool observation,
> every compaction, and a replay built from an append-only event log."

Show `README.md` MVP flow (one line, do not read it out).

## 1:00–2:30 — The failed run (timeout, replay, integrity)

Live demo:

```bash
python examples/simple_agent.py          # 30s: a healthy run's event trace
python -m pytest tests/test_timeouts.py -q   # the failure paths
```

Talking points while the tests run:

- **Provider and tool deadlines are explicit configuration** — and a breach
  is terminal: `AgentFailed` carries `phase`, the timeout category and a
  redacted diagnostic. Timed-out work can never be reported as success.
- The event log stays well-formed even in failure: a timed-out tool still
  closes its boundary (`ToolCallFinished` with `ok=false`), so replay keeps
  its start/finish pairing.
- **Replay integrity is formal**: a truncated or corrupted trace is rejected
  with stable error codes (`SEQUENCE_GAP`, `MIXED_TRACE_IDS`, …) while a
  genuinely running session replays as `running`. Corruption can never look
  like a valid replay.

If asked "what happens on disk?": schema is versioned, migrations are one
entry point, the session projection and the event row are written in one
transaction, and a rebuild path re-derives the projection from the log.

## 2:30–4:00 — The A/B result (three scenarios, two weight sets)

Live demo:

```bash
python examples/compaction_compare.py
```

Point at three things in the output:

1. **Raw metrics first, composite second.** Both strategies complete every
   scenario; the differences are in context efficiency and semantic
   survival, not in completion.
2. **The failure case is the headline**: in `critical_early_decision`,
   `keep_recent_summary` loses the early decision (its documented contract
   keeps only the earliest task + latest tool result) while `semantic_state`
   preserves it in structured state. The suite reports that failure instead
   of hiding it inside an average.
3. **Sensitivity is disclosed**: the winner is the same under the documented
   weights and the alternate `reliability-heavy-v1` set, with deltas shown —
   the verdict does not hinge on one hidden weighting choice.

Close with the auditability claim: every snapshot records its estimator;
comparisons reject estimator mismatch; the deterministic formula is anchored
to versioned fixtures (Chinese text, JSON, tool schemas, empty content).

## 4:00–5:00 — Engineering choices and limits

- Events are the only integration boundary (no UI/replay/eval reaches into
  the loop); snapshots are first-class; everything is offline-deterministic
  by construction (scripted provider, fixed fixtures, byte-stable suites).
- 198 offline tests, ruff + mypy `--strict`, 95% core coverage with an
  enforced 80% floor, Windows + Linux CI, wheel build smoke.
- **Limits, stated plainly**: the token estimator is an engineering proxy
  (not billing tokens); single-process SQLite; no auth; task completion is
  rule-based; the API runs offline scenarios synchronously. This is a
  complete MVP — deliberately not production-ready.
- If asked about real providers: the `openai-compat` adapter behind the
  optional extra has been smoke-tested against a real endpoint — happy path
  plus three injected failures (timeout, invalid credential, unroutable
  base URL), all typed and redacted; the credentials never appeared in any
  output (docs/evidence/release-v0.1.0/report.md §3.1). Production work
  would start at auth, concurrency and streaming.

## Fallbacks

- No terminal available? The screenshots in
  `docs/evidence/review-r9-r10/screenshots/` show the Inspector (deep link,
  valid and corrupt traces).
- Short on time? Run only the compaction compare — it contains the whole
  A/B argument in one screen.
