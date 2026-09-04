# Five-minute pitch structure

| Time | Beat |
| --- | --- |
| 0:00–0:30 | The problem. Two files that never line up; a person matching them by hand every morning. |
| 0:30–1:00 | `make demo` on screen. 65 credits, 55 settlements, one command, ~1 ms. |
| 1:00–2:00 | The report. Match rate, precision, **0 false matches** — and the `0/2` on `ambiguous_pair`, explained as the deliberate refusal it is. |
| 2:00–3:00 | **The decision that matters.** We built the model matcher, measured it against the rules baseline, and deleted it. Show `docs/decisions.md` D2. Explain where the model went instead, and why the exception queue is the real bottleneck. |
| 3:00–4:00 | Safety, live. Show the gate rejecting a confident hallucination in `tests/`. Closed enum, code-owned GL table, claim-once, append-only audit. |
| 4:00–4:30 | Cost-calibrated threshold. Why 0.8 is a guess and this is not. |
| 4:30–5:00 | The honest limitations slide. No live integration, simulated bank data, offline classifier circularity. Say it out loud — it is the strongest thing in the pitch. |

Do not open an IDE and scroll. Run the command, read the report, show two files.
