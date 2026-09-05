# Five-minute pitch: recording plan

Five minutes is short. **Do not tour the code.** Run one command, read the
output, open two files. Everything below is timed against that.

## Before you hit record

```bash
git pull                    # make sure you have the live run committed
make demo                   # do a dry run so nothing surprises you on camera
```

Have exactly three things open, nothing else:

1. A terminal, large font, in the repo
2. `out/dashboard.html` in a browser tab
3. `docs/failures.md` in an editor or on GitHub

Close Slack, email, notifications. Record at 1080p.

---

## The script

### 0:00 – 0:25 · The problem

> "A merchant on Razorpay has two records of the same money. Razorpay's
> settlement report, and their bank statement. Nothing joins them — Razorpay
> never sees the bank statement, so the last hop, the one that proves the money
> actually arrived, gets done by a person opening two files every morning.
>
> This closes that loop."

Show the two CSVs side by side for two seconds. Don't linger.

### 0:25 – 1:10 · Run it

Type `make demo` and let it run. While it goes:

> "Sixty-five bank credits against fifty-five settlements. Synthetic, seeded,
> and the generator emits its own answer key — so every number you're about to
> see is scored against ground truth, not asserted."

### 1:10 – 1:55 · Read the result

> "Eighty-one and a half percent matched. A hundred percent precision — and
> zero false matches, which held across a hundred unseen seeds.
>
> Now look at `ambiguous_pair`: zero out of two. That's deliberate. Those are
> two settlements of identical amount on the same day, and the narration is
> just `SETTLEMENT BULK` with no reference. **The information needed to tell
> them apart does not exist.** Guessing would score fifty percent and put a
> wrong number in a ledger. So they get escalated.
>
> A reconciler that never says *I don't know* isn't trustworthy. It's
> unmeasured."

### 1:55 – 3:15 · The decision that matters

**This is the centre of the pitch. Slow down here.**

> "The plan was the obvious one — rules match what they can, and an LLM reads
> the messy bank narrations that regexes can't parse.
>
> I built the deterministic tiers first, specifically to get the baseline the
> model would have to beat. The baseline came back at a hundred percent
> precision with zero false positives — and every narration I'd written to
> defeat a regex was already resolved.
>
> The rows left over weren't a gap a model could close. Ten were credits that
> aren't settlements at all, where leaving them unmatched is the *correct*
> answer. Two were the ambiguous pair.
>
> So I deleted the LLM matcher. It would have added false positives, not
> recall."

Open `docs/decisions.md`, show D2, then:

> "The model does exception triage instead — which is the bottleneck the brief
> itself names: *verification capacity, not generation speed*. Deciding that a
> GST refund line is a tax refund and not our settlement is genuinely semantic.
> As rules it's a hardcoded vendor list that's stale on day one."

### 3:15 – 3:55 · Why you can trust it with money

> "The model proposes a label from a closed six-value enum. It never names an
> account, an amount, or a settlement — the GL account is a table lookup in
> code.
>
> And it doesn't get the last word. The gate re-derives evidence the model
> never saw. If the model calls a credit third-party but its narration carries
> a reference resolving to a settlement on file, the proposal is rejected —
> structurally, not by asking the prompt nicely."

Show `test_hallucinated_high_confidence_is_still_gated` passing.

> "A settlement is claimable exactly once. Every rejection routes to suspense
> for a human. Every row writes one append-only audit record."

### 3:55 – 4:35 · What broke

> "This ran live on Gemini. First run, eight of twelve calls got rate-limited —
> and the report printed twenty-five percent accuracy, as if the model had got
> three-quarters of its answers wrong. It had answered four rows and got three
> right.
>
> Availability and accuracy are different failures with different fixes, and my
> report was blending them. Same run, throughput read three rows a second,
> because I was timing network waits alongside a matcher that does eighty
> thousand.
>
> Neither bug was visible offline. A stub returns instantly."

Open `docs/failures.md`.

> "Sixteen of these are written down. Some are fixes to earlier fixes."

### 4:35 – 5:00 · Close

> "What this doesn't claim: there's no live Razorpay integration — no key, no
> calls. The settlement side is generated to the verified entity contract so a
> real endpoint substitutes at one seam, but I'm not claiming an integration I
> don't have. Bank statements are simulated, because no Razorpay API exposes
> one.
>
> Everything I showed regenerates from committed code with `make demo`.
> Thank you."

---

## Recording it

**No install, browser only:** [Loom](https://loom.com) — free tier caps at five
minutes, which is exactly the limit. Gives you a shareable link immediately.

**Windows, built in:** `Win + G` opens Game Bar, record the screen, saves an MP4.

**Mac, built in:** `Cmd + Shift + 5`, record selected portion or whole screen.

**Anything, more control:** OBS Studio, free.

Then upload to **YouTube as unlisted** and put that link in the form. Unlisted
is fine — the brief says so.

## Two things that lose marks

- **Don't scroll code.** Judges can read the repo. The video is for the parts
  the repo can't say out loud.
- **Don't oversell.** The limitations slide at 4:35 is not a weakness. Saying
  what you didn't do is the fastest way to make everything else believable.

## If you fluff a line

Keep going. One retake of the whole thing beats twelve edits, and nobody is
scoring your diction.
