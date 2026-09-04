# Architecture

```
  bank_statement.csv          settlements.csv
  (SIMULATED)                 (VERIFIED contract)
        |                            |
        +------------+---------------+
                     v
            recon/sources.py          <- the one seam a live
                     |                   GET /v1/settlements swaps into
                     v
        +-------------------------------------+
        |  DETERMINISTIC MATCHER              |
        |  recon/extract.py + recon/match.py  |
        |                                     |
        |  T1  labelled UTR + amount exact    |
        |  T1  bare ref + amount corroborates |
        |  T2  UTR + variance explained by    |
        |      non-zero fees/tax              |
        |  T3  unique amount + date window    |
        |                                     |
        |  claim-once enforced here           |
        +------------------+------------------+
                           |
              matched -----+----- unresolved
                 |                    |
                 |         reason in {NO_CANDIDATE, AMBIGUOUS}?
                 |            |                        |
                 |           yes                      no  (terminal:
                 |            |                            AMOUNT_OUT_OF_TOLERANCE,
                 |            v                            ALREADY_CLAIMED)
                 |   +------------------+                  |
                 |   | MODEL TRIAGE     |                  |
                 |   | recon/triage.py  |                  |
                 |   | closed enum,     |                  |
                 |   | schema-validated |                  |
                 |   +--------+---------+                  |
                 |            | proposal (advisory)        |
                 |            v                            |
                 |   +--------------------------+          |
                 |   | POLICY GATE              |          |
                 |   | recon/policy.py          |          |
                 |   | - label in closed set?   |          |
                 |   | - confidence >= calibrated
                 |   | - two-signal agreement   |          |
                 |   | - GL account from table  |          |
                 |   +--------+--------+--------+          |
                 |            |        |                   |
                 |       accepted   rejected --> suspense  |
                 |            |                     |      |
                 v            v                     v      v
        +-------------------------------------------------------+
        |  APPEND-ONLY AUDIT  (out/audit.jsonl)                 |
        +---------------------------+---------------------------+
                                    v
                    evaluation/score.py  vs ground_truth.csv
                                    v
                     report + value-ranked exception queue
```

## Why the model sits where it does

It is downstream of a matcher that has already run to exhaustion, so it can
never pre-empt a rule and never sees a matched row. It is upstream of a gate
that can overrule it from evidence it never saw. Its output is a label from a
closed enum, so it cannot name an account, an amount, or a settlement.

The blast radius of a bad model output is therefore: **an exception lands in the
wrong bucket of a queue a human was already going to read.** It cannot produce a
false match, and it cannot move money.

## Terminal vs triageable

Only `NO_CANDIDATE` and `AMBIGUOUS` reach triage. A row the matcher rejected
because a reference matched with the wrong amount is a *discrepancy*, not a
classification problem — a second opinion from a model would be the wrong tool,
so it goes straight to a human. Pinned by
`test_terminal_match_failures_never_reach_triage`.

## Module map

| Module | Responsibility |
| --- | --- |
| `models.py` | Frozen dataclasses; malformed rows fail at construction |
| `sources.py` | CSV readers; the live-API substitution seam |
| `generate.py` | Seeded synthetic data + ground-truth answer key |
| `extract.py` | Narration parsing, split by precision (labelled vs bare) |
| `match.py` | Tiered matcher, claim tracking |
| `triage.py` | The only model call in the system |
| `offline_triage.py` | `SIMULATED` stand-in, labelled everywhere |
| `policy.py` | The gate; GL account table |
| `audit.py` | Append-only JSONL |
| `pipeline.py` | Orchestration |
| `cli.py` | Entry point and report rendering |
