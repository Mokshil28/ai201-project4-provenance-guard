# Provenance Guard — Planning & Spec

A backend a creative-sharing platform can plug into to classify submitted text as
human- or AI-written, score confidence in that classification, surface a
transparency label to readers, and let creators appeal a decision.

This is the **implementation-ready spec** (Milestone 2). It answers the five core
design questions with concrete numbers, holds the exact label text, carries the
architecture diagram forward from Milestone 1, and plans how each build milestone
will use AI tooling.

**Guiding principle:** on a writing platform, a **false positive (flagging a
human's work as AI) is worse than a false negative.** Every threshold, weight,
and piece of label wording below is biased toward that asymmetry.

---

## 1. Detection Signals

Two **genuinely distinct** signals — one *structural*, one *semantic*. They fail
in different ways, which is exactly why combining them beats either alone.

### Signal 1 — Stylometric heuristics (structural, pure Python)

**Measures** the statistical *shape* of the writing from three sub-metrics:

| Sub-metric | Definition | AI tends to… | Human tends to… |
|---|---|---|---|
| Sentence-length variance | std-dev of words-per-sentence, normalized | be uniform (low variance) | be bursty (high variance) |
| Type-token ratio (TTR) | unique words ÷ total words | sit in a safe mid-range | be more extreme (very high or low) |
| Punctuation density & variety | punctuation marks per word + count of distinct mark types | be regular, few mark types | be idiosyncratic (dashes, semicolons) |

**Output.** A float `s_stylo ∈ [0, 1]`, where **higher = more AI-like**, plus the
raw sub-metric values for the audit log:
```json
{ "score": 0.80, "sentence_variance": 0.12, "ttr": 0.34, "punct_density": 0.03, "punct_variety": 2 }
```
Each sub-metric is normalized to a `[0,1]` "AI-likeness" contribution, then
averaged (equal weight) into `s_stylo`. Uniform → high; varied → low.

**Blind spot.** Fooled by *register*, not substance. Plain-but-human writing
(minimalist poetry, terse technical prose) looks "AI-uniform" and can be
false-flagged; an LLM prompted to "write with varied, bursty sentences" defeats
it. It is meaning-blind, and unstable on short texts.

### Signal 2 — LLM classifier (semantic, Groq `llama-3.3-70b-versatile`)

**Measures** a holistic judgment of whether the text *reads* as human-written —
coherence, idea development, voice, and the tells of generated prose (hedging,
over-explaining, tidy structure) all at once.

**Output.** A structured JSON response coerced to a float `s_llm ∈ [0, 1]`
(probability the text is AI-generated) plus a short rationale:
```json
{ "score": 0.30, "rationale": "distinctive voice and uneven imagery; reads human" }
```
Prompt asks the model to return **only** `{ "p_ai": <0..1>, "rationale": "<short>" }`.
If the call fails / times out / returns junk, we **degrade gracefully**: fall
back to `s_stylo` alone and record `llm.score = null, llm.error = "..."` in the
log (see §5 edge cases).

**Blind spot.** No LLM is a reliable ground-truth detector — AI-text detection is
unsolved. It can be overconfident, biased against non-native or unusual-but-human
styles, sensitive to prompt wording, and non-deterministic.

### Combining the two signals

```
p_ai       = 0.6 * s_llm + 0.4 * s_stylo          # weighted blend, LLM leads
agreement  = 1 - |s_llm - s_stylo|                 # 1 = perfect agreement, 0 = opposite
raw_conf   = 2 * |p_ai - 0.5|                       # distance from the "no idea" midpoint
confidence = raw_conf * agreement                   # disagreement DEDUCTS confidence
```

- **Why LLM weighted higher (0.6):** it reads meaning; stylometry is content-blind.
- **Why multiply by `agreement`:** if the two signals point opposite ways, that
  disagreement is itself a signal — we lower confidence rather than average into
  a false verdict. This is the mechanism that catches the false-positive case.
- If the LLM signal is unavailable, `p_ai = s_stylo`, `agreement = 1` is *not*
  assumed — instead confidence is capped at `0.50` (single-signal ceiling) so a
  lone heuristic can never produce a "high-confidence" verdict.

---

## 2. Uncertainty Representation

**Two separate numbers, on purpose:**
- `p_ai` — *which way* the evidence leans (0 = human, 1 = AI).
- `confidence` — *how much* to trust that lean (0 = coin flip, 1 = certain).

**What a confidence of 0.6 means to the system:** the two signals broadly agree
*and* the combined score sits clearly off the 0.5 midpoint — enough to state a
verdict, but with visible reservation. It is **not** "60% chance it's AI"; that's
what `p_ai` is for.

Confidence bands (human meaning):

| confidence | meaning |
|---|---|
| 0.00 – 0.35 | no meaningful signal / signals disagree → **uncertain** |
| 0.35 – 0.65 | moderate |
| 0.65 – 1.00 | strong |

**Verdict thresholds** — deliberately asymmetric (higher bar to accuse of AI):

| Verdict | Rule |
|---|---|
| **likely_human** | `p_ai ≤ 0.45` **and** `confidence ≥ 0.35` |
| **likely_ai** | `p_ai ≥ 0.65` **and** `confidence ≥ 0.50` (stricter both ways) |
| **uncertain** | everything else |

This makes the "uncertain" band **wider on the AI side**: borderline work falls
into *uncertain*, not *likely_ai*. A single weak signal (confidence ≤ 0.50) can
never trigger the AI verdict.

**This is not a binary flip at 0.5.** Worked examples:

| s_stylo | s_llm | p_ai | agreement | confidence | verdict |
|---|---|---|---|---|---|
| 0.90 | 0.92 | 0.912 | 0.98 | 0.807 | likely_ai (high) |
| 0.10 | 0.08 | 0.088 | 0.98 | 0.807 | likely_human (high) |
| 0.80 | 0.30 | 0.500 | 0.50 | 0.000 | uncertain (signals clash — the FP case) |
| 0.55 | 0.60 | 0.580 | 0.95 | 0.152 | uncertain (near midpoint) |
| 0.70 | 0.68 | 0.688 | 0.98 | 0.369 | uncertain (leans AI but conf < 0.50) |

**How we'll test that scores are meaningful (M4):** run a fixed corpus of
clearly-human samples (published poems/essays), clearly-AI samples (raw model
output), and deliberate borderline/adversarial samples; assert human→low `p_ai`,
AI→high `p_ai`, borderline→low confidence, and that all three label variants are
reachable. Documented in the README with the sample outputs.

---

## 3. Transparency Label Design

Three variants. Non-technical, plain language. AI wording is **hedged and never
accusatory** ("signs of", "estimate", not "this IS AI"). Each label carries the
numeric confidence so a reader can gauge it. `{confidence_pct}` is
`round(confidence * 100)`.

### Variant A — High-confidence AI (`likely_ai`)
> ⚠️ **Likely AI-generated.** Our automated analysis found strong signs this text
> may have been produced with AI assistance (confidence: **{confidence_pct}% —
> HIGH**). This is an estimate, not a certainty, and it is not an accusation. If
> you're the creator and this is wrong, you can appeal and a human will review it.

### Variant B — High-confidence human (`likely_human`)
> ✓ **Likely human-written.** Our automated analysis found strong signs this text
> was written by a person (confidence: **{confidence_pct}% — HIGH**). This is an
> automated estimate, not a guarantee of authorship.

### Variant C — Uncertain (`uncertain`)
> ❔ **Attribution uncertain.** Our automated analysis couldn't confidently
> determine whether this text was written by a person or generated with AI
> (confidence: **{confidence_pct}% — LOW**). Please treat the origin as
> inconclusive. No attribution claim is being made.

The confidence word (HIGH/MODERATE/LOW) is derived from the §2 confidence bands.

---

## 4. Appeals Workflow

**Who can appeal.** The content's creator. In this backend they're identified by
the `content_id` returned at submission (and optional `creator_id`); a real
platform would gate this behind that creator's auth session.

**What they provide.** `content_id` + a free-text `reason` (their reasoning for
why the classification is wrong). Reason is required and non-empty.

**What the system does on receipt:**
1. Look up the original decision by `content_id` (`404` if unknown).
2. Create an **appeal record**: `appeal_id`, `content_id`, `reason`, timestamp,
   linked to the original decision snapshot.
3. Flip the content's **status** from `classified` → `under_review`.
4. Write an **audit-log entry** of type `appeal` alongside the original
   `decision` entry (the log keeps both — nothing is overwritten).
5. Return `appeal_id` + new `status`.

No automated re-classification (not required) — a human reviewer resolves it.

**What a human reviewer sees** (via `GET /appeals` or `GET /log`) per queued item:

| Field | Example |
|---|---|
| content_id / appeal_id | `c_a1b2c3` / `a_9z8y7x` |
| status | `under_review` |
| original verdict + confidence | `likely_ai`, 0.81 |
| p_ai + per-signal breakdown | 0.91 · stylo 0.90 / llm 0.92 |
| text snippet (or hash) | "The quiet hours fold…" |
| creator's appeal reason | "This is my own poem, written in 2019." |
| timestamps | submitted / appealed |

---

## 5. Anticipated Edge Cases

Specific scenarios this system handles poorly, and what we do about each:

1. **Minimalist / repetitive poem, simple vocabulary, even line lengths.**
   Stylometry sees low variance + low TTR + sparse punctuation → scores it
   *AI-like* (~0.80) even though it's authentically human. **Mitigation:** the
   semantic LLM signal usually reads it as human; the resulting *disagreement*
   drives `confidence` down and the verdict to **uncertain**, not `likely_ai`.
   This is the core false-positive scenario (traced in §Architecture narrative).

2. **Very short submissions (< ~40 words / 2–3 sentences).** Stylometric
   statistics are unstable — TTR trivially approaches 1.0, variance is
   meaningless with 2 sentences. **Mitigation:** below a minimum token threshold
   we down-weight / flag the stylometric signal and cap confidence at LOW so we
   never over-claim on too little data. Documented in the response
   (`"note": "text too short for reliable stylometry"`).

3. **Non-native-English human writing.** The LLM classifier can be biased toward
   "AI" for grammatically-regular non-native prose. **Mitigation:** the
   asymmetric thresholds (higher bar for `likely_ai`, confidence ≥ 0.50 required)
   plus the always-available appeal path reduce the harm of this bias.

4. **Human-edited AI drafts (hybrid).** Neither signal is clean — genuinely
   mixed provenance. **Mitigation:** these correctly land in **uncertain**; the
   label states no attribution claim is being made, which is the honest answer.

---

## Architecture

**Submission flow (2–3 sentence narrative):** A `POST /submit` request passes the
rate limiter first (floods are rejected before we pay for an LLM call), then the
raw text is scored independently by the stylometric heuristic and the Groq LLM.
The two scores are blended into `p_ai` and a `confidence` (disagreement deducts
confidence), which select one of three transparency labels; the full decision is
persisted to the audit log and returned as JSON.

**Appeal flow:** A `POST /appeal` looks up the original decision by `content_id`,
records the creator's reason, flips the content's status to `under_review`, and
writes an `appeal` entry into the same audit log — nothing is overwritten, so a
human reviewer sees the original decision and the appeal side by side.

### Submission flow
```
                 raw text (+creator_id)
   [Client] ─────────────────────────────▶ [POST /submit]
                                                  │
                                    request │ (checked before any work)
                                                  ▼
                                          [Rate Limiter] ──429──▶ [Client] (rejected)
                                                  │ allowed: raw text
                                                  ▼
                              ┌────────── Detection Pipeline ──────────┐
                              │                                        │
                   raw text   ▼                            raw text    ▼
            [Signal 1: Stylometric]                 [Signal 2: LLM / Groq]
                   │ s_stylo∈[0,1] (+submetrics)          │ s_llm∈[0,1] (+rationale)
                   └───────────────┬────────────────────────┘
                                   ▼  (s_stylo, s_llm)
                          [Confidence Scoring]
                                   │  p_ai = 0.6·s_llm + 0.4·s_stylo
                                   │  confidence = 2·|p_ai−0.5| · (1−|s_llm−s_stylo|)
                                   ▼
                          [Transparency Label]
                                   │  verdict + confidence → variant A/B/C + text
                                   ▼
                          [Audit Log / SQLite] ──── persists: id, scores, p_ai,
                                   │                 confidence, verdict, label, ts
                                   │  content_id + full result
                                   ▼
                          [JSON Response] ─────────▶ [Client]
```

### Appeal flow
```
              content_id + reason
   [Client] ───────────────────────▶ [POST /appeal]
                                            │ look up content_id
                                            ▼
                                   [Content Store] ──404──▶ [Client] (not found)
                                            │ found
                                            ▼
                                   [Status Update]  status → "under_review"
                                            │  appeal record (reason) + original decision
                                            ▼
                                   [Audit Log / SQLite]  logs appeal alongside decision
                                            │  appeal_id + status
                                            ▼
                                   [JSON Response] ────────▶ [Client]
```

---

## API Surface (the contract)

| Method | Path | Accepts | Returns |
|---|---|---|---|
| `POST` | `/submit` | `{ "text": str, "creator_id"?: str }` | `content_id`, `verdict`, `confidence`, `p_ai`, `signals`, `label` |
| `POST` | `/appeal` | `{ "content_id": str, "reason": str }` | `appeal_id`, `status: "under_review"`, message |
| `GET` | `/appeals` | — | appeal queue (reviewer view, §4) |
| `GET` | `/log` | optional `?limit=` | audit-log entries (decisions + appeals) |
| `GET` | `/health` | — | `{ "status": "ok" }` |

**`POST /submit` response (draft):**
```json
{
  "content_id": "c_a1b2c3",
  "verdict": "uncertain",
  "confidence": 0.00,
  "p_ai": 0.50,
  "signals": {
    "stylometric": { "score": 0.80, "sentence_variance": 0.12, "ttr": 0.34, "punct_density": 0.03 },
    "llm":         { "score": 0.30, "rationale": "distinctive voice; reads human" }
  },
  "label": {
    "variant": "uncertain",
    "text": "❔ Attribution uncertain. Our automated analysis couldn't confidently determine…"
  }
}
```

Errors: `400` (missing/invalid fields), `404` (`content_id` not found on appeal),
`429` (rate limit exceeded on `/submit`).

**Rate limiting (numbers finalized in M5):** working plan — `10/minute` and
`100/day` per client on `/submit`. Rationale: a real creator submits a handful of
pieces at most; anything above ~10/min is automation or an abuse flood, and the
daily cap bounds LLM cost per client. Final values + reasoning go in the README.

---

## AI Tool Plan

How each build milestone will use an AI coding tool, what spec it gets, and how
output is verified. The tool for every milestone is given the **§Architecture
diagram** plus the sections listed.

### M3 — Submission endpoint + first signal
- **Spec provided:** §1 (esp. Signal 1 stylometric + output format), §API
  Surface, §Architecture diagram.
- **Ask it to generate:** a Flask app skeleton with `POST /submit` and
  `GET /health`, plus a pure-Python `stylometric_score(text) -> dict` returning
  `{score, sentence_variance, ttr, punct_density, punct_variety}`.
- **Verify:** call `stylometric_score` directly (not through HTTP) on 4–5 hand
  inputs — a bursty human paragraph, uniform AI-style text, a 2-sentence stub —
  and confirm uniform text → high score, varied text → low, short text flagged.
  Only then wire it into `/submit`.

### M4 — Second signal + confidence scoring
- **Spec provided:** §1 (Signal 2 LLM + combining formula), §2 (uncertainty +
  thresholds + worked examples), §Architecture diagram.
- **Ask it to generate:** `llm_score(text) -> dict` (Groq call, JSON-coerced,
  graceful fallback) and `combine(s_stylo, s_llm) -> {p_ai, confidence, verdict}`
  implementing the exact formulas and thresholds from §1–§2.
- **Verify:** run the fixed corpus (clear-human / clear-AI / borderline); assert
  human→low `p_ai`, AI→high `p_ai`, disagreement→low confidence, and that the
  §2 worked-example rows reproduce. Confirm all three verdicts are reachable.

### M5 — Production layer (labels, appeals, rate limit, audit log)
- **Spec provided:** §3 (label variants, verbatim), §4 (appeals workflow), §API
  Surface, §Architecture appeal-flow diagram.
- **Ask it to generate:** `make_label(verdict, confidence) -> {variant, text}`
  emitting exactly variants A/B/C with the confidence interpolated; the
  `POST /appeal`, `GET /appeals`, `GET /log` endpoints; Flask-Limiter config; and
  SQLite audit logging for both `decision` and `appeal` entries.
- **Verify:** submit crafted inputs that force each of the three labels; submit an
  appeal and confirm status flips to `under_review` and both a `decision` and an
  `appeal` row appear in `GET /log`; hammer `/submit` to confirm `429`.

---

## Checkpoint status (Milestone 2)

- [x] §1 detection signals — 2+ distinct, output format, combination formula.
- [x] §2 uncertainty — meaning of a score, calibration, three-way thresholds
      (not a binary flip at 0.5; worked examples included).
- [x] §3 three label variants written out verbatim (AI / human / uncertain).
- [x] §4 appeals workflow — who, what info, status change, logging, reviewer view.
- [x] §5 ≥2 specific edge cases with mitigations.
- [x] §Architecture — Milestone 1 diagram + 2–3 sentence narrative.
- [x] §AI Tool Plan — M3, M4, M5 with sections, requests, verification.
