# Razorpay capability register

Every capability this project touches, labelled per `ENGINEERING.md`. Fetched from
official docs on 2026-09-04.

## `VERIFIED` — Settlements entity

<https://razorpay.com/docs/api/settlements/>

```json
{
  "id": "setl_7IZKKI4Pnt2kEe",
  "entity": "settlement",
  "amount": 50000,
  "status": "processed",
  "fees": 0,
  "tax": 0,
  "utr": "1597813219e1pq6w",
  "created_at": 1509622307
}
```

| Field | Verified behaviour |
| --- | --- |
| `id` | `setl_` + 14 alphanumeric chars. **Not** a zero-padded counter. |
| `amount` | Integer, smallest currency unit (₹500 → `50000`). Paise. |
| `status` | One of `created`, `processed`, `failed`. |
| `fees` | Integer. *"In case of a normal settlement the fee charge will be 0."* |
| `tax` | Integer, tax on those fees. Also 0 for a normal settlement. |
| `utr` | **Alphanumeric**, e.g. `1597813219e1pq6w`. Not purely numeric. |
| `created_at` | Unix epoch seconds, not a date string. |

Three consequences we acted on, each a correction to an earlier assumption:

1. **UTRs are alphanumeric.** A naive `\d{12}` narration regex finds nothing on
   real UTRs, or worse, latches onto an unrelated numeric reference in the
   narration. This is a large part of why deterministic extraction alone is
   insufficient, and it is the honest justification for the model tier.
2. **For a normal settlement `fees` and `tax` are 0**, because Razorpay deducts
   fees per payment rather than per settlement. So `amount` is what the bank
   should credit — we do not subtract fees again. Modelling it otherwise would
   have manufactured a variance class that does not exist.
3. **`created_at` is epoch seconds**, so the settlement date is derived, not given.

## `VERIFIED` — Fetch Settlement Recon Details

```
GET https://api.razorpay.com/v1/settlements/recon/combined?year=2022&month=06&day=11
```

Basic auth with `[YOUR_KEY_ID]:[YOUR_KEY_SECRET]`. Returns
`{"entity": "collection", "count": N, "items": [...]}` where items carry an
`entity_id` such as `pay_DEXrnipqTmWVGE`.

Razorpay ships a settlement-recon report of its own. Worth being straight about
what that means for this project: it reconciles **Razorpay's own view** —
payments to settlements. It does not and cannot reconcile the merchant's **bank
statement** against those settlements, because Razorpay never sees the bank
statement. That gap is exactly the loop we close, and it is where the messy
narration text lives.

## `VERIFIED` — Gemini API (the triage tier's default provider)

`POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`
with an `x-goog-api-key` header. Structured output via
`generationConfig.responseMimeType: "application/json"` plus a `responseSchema`
in the OpenAPI subset (uppercase type names, no `additionalProperties`).
Confirmed reachable; a keyless call returns a documented
`403 PERMISSION_DENIED / "Method doesn't allow unregistered callers"`, which is
the API answering, not a network block.

Called over `urllib` rather than a vendor SDK: the core of this project is
standard library only, and one JSON POST does not justify a dependency.

## `SIMULATED` — Bank statement

No Razorpay API supplies this; it comes from the merchant's bank. We generate
NEFT/RTGS/IMPS credit lines modelled on Indian bank statement formats.
Labelled `SIMULATED` everywhere it surfaces. Realistic in shape, not sourced
from a real institution.

## `UNAVAILABLE` in this build — live API calls

We hold no Razorpay API key and make **no live calls**. The settlement side is
generated to the verified entity contract above so a live `GET /v1/settlements`
could be substituted at one seam (`recon/sources.py`) without touching the
matcher. We do not claim a working integration, and nothing in the repo should.

## `INFERRED` — settlement-to-bank-credit timing

We model bank credit landing one day after settlement. Consistent with standard
T+1 settlement, but not a documented guarantee. The matcher therefore treats the
date as a **tolerance window**, never an equality test.
