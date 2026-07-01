# Provenance Guard — Planning

A backend system a creative-sharing platform can plug into to classify submitted
text as human- or AI-written, score confidence in that classification, surface a
transparency label to readers, and let creators appeal a decision.

This document is the design record for **Milestone 1**: architecture, detection
signals, the false-positive scenario, the API contract, and the flow diagrams.
No implementation code is written yet — this is the contract everything else
will implement.

---

## 1. Architecture Narrative — the path of one submission

Follow a single poem from the moment a creator hits "submit" to the label a
reader sees.

1. **Client → `POST /submit`.** The platform sends the raw text (plus optional
   metadata like a creator id). The request first passes through the
   **rate limiter**, which checks whether this client has exceeded its allowed
   submission volume. If it has, the request is rejected with `429` before any
   detection work is done (detection costs an LLM call — we don't want to pay
   for floods).

2. **Detection pipeline — Signal 1 (Stylometric heuristics).** The text is run
   through a pure-Python analyzer that measures *structural* properties:
   sentence-length variance, type-token ratio (vocabulary diversity), and
   punctuation density. These are combined into a single stylometric score in
   `[0,1]` where higher = more AI-like (more uniform, less varied). This signal
   is deterministic, free, and always available even if the network is down.

3. **Detection pipeline — Signal 2 (LLM classifier via Groq).** The same text is
   sent to `llama-3.3-70b-versatile` with a structured prompt asking it to judge
   whether the writing reads as human or AI-generated and to return a probability
   and a short rationale. This captures *semantic / holistic* coherence that raw
   statistics miss.

4. **Confidence scoring.** The two signal scores are combined into a single
   `p(AI)` via a documented weighted blend. From `p(AI)` we derive:
   - a **verdict** bucket: `likely_human`, `uncertain`, or `likely_ai`, and
   - a **confidence** value that expresses how far the combined score sits from
     the 0.5 "no idea" midpoint. The two signals *agreeing* raises confidence;
     the two signals *disagreeing* lowers it (an explicit uncertainty penalty).

5. **Transparency label.** The verdict + confidence select one of three
   plain-language label variants (high-confidence human, uncertain,
   high-confidence AI). The uncertain band is deliberately wide, and the AI
   labels are worded cautiously, because a false positive (calling a human's
   work AI) is the costliest error on a writing platform.

6. **Persistence + audit log.** A record is written to SQLite: content id, a hash
   or snippet of the text, both raw signal scores, the combined `p(AI)`,
   confidence, verdict, label text, and a timestamp. This row is the canonical
   evidence and the thing an appeal attaches to.

7. **Response → client.** The endpoint returns a structured JSON body:
   `content_id`, `verdict`, `confidence`, `p_ai`, the per-signal breakdown, and
   the `label` text ready to render.

**Appeal path (later, out of band).** A creator who disagrees calls
`POST /appeal` with the `content_id` and their reasoning. The system records the
appeal *alongside* the original decision, flips the content's status to
`under_review`, and writes an audit entry. No automated re-classification is
required — a human reviewer picks it up.

---

## 2. Detection Signals

The system uses **two genuinely distinct signals**: one *structural*, one
*semantic*. They fail in different ways, which is exactly why combining them is
more informative than either alone.

### Signal 1 — Stylometric heuristics (structural, pure Python)

**What it measures.** Statistical shape of the writing, from three sub-metrics:
- **Sentence-length variance** — how much sentence length swings across the text.
- **Type-token ratio (TTR)** — unique words ÷ total words = vocabulary diversity.
- **Punctuation density** — punctuation marks per word, and variety of marks used.

**Why it separates human from AI.** Default LLM prose tends to be *uniform*:
even sentence lengths, "safe" mid-range vocabulary, regular punctuation. Human
writing is *bursty* — a three-word sentence next to a forty-word one, odd word
choices, dashes and semicolons used idiosyncratically. High uniformity nudges
the score toward AI; high variability nudges it toward human.

**Blind spot.** It is fooled by *style*, not substance. A human writing in a
plain, even register (technical docs, a terse minimalist poem) looks "AI-uniform"
and can be false-flagged. Conversely, an LLM explicitly prompted to "write with
varied, bursty sentences" can defeat it. It also has no idea what the text
*means* — it can't tell an insightful essay from fluent nonsense. Short texts
(a few sentences) give unstable statistics.

### Signal 2 — LLM classifier (semantic, Groq `llama-3.3-70b-versatile`)

**What it measures.** A holistic judgment: does this read like something a person
wrote? The model weighs coherence, idea development, voice, and the subtle
tells of generated text (hedging, over-explaining, tidy structure) all at once —
things no single statistic captures.

**Why it separates human from AI.** LLMs are good at recognizing the "texture"
of machine writing because it resembles their own output distribution. It reads
*content and meaning*, which the stylometric signal is blind to.

**Blind spot.** It is not a reliable ground-truth detector — no LLM is; AI-text
detection is an unsolved problem. It can be overconfident, is sensitive to prompt
wording, can be biased against non-native English or unusual-but-human styles,
and is non-deterministic (same text, slightly different score). It also depends
on the network and the Groq quota — if the call fails we must degrade
gracefully to the stylometric signal alone.

**Why the pair is strong.** The stylometric signal is deterministic and
content-blind; the LLM signal is content-aware but noisy. When they *agree* we
can be genuinely confident. When they *disagree*, that disagreement is itself
the signal — we lower confidence and push the verdict toward "uncertain" rather
than guess.

---

## 3. The False-Positive Problem (traced through the system)

**Scenario.** A poet submits a spare, minimalist poem — short, even-length lines,
plain vocabulary. This is authentically human but *statistically uniform*.

1. **Signal 1 (stylometric)** sees low variance + low TTR + sparse punctuation →
   scores it AI-like, say `0.80`.
2. **Signal 2 (LLM)** reads the imagery and voice, judges it human → `0.30`.
3. **Confidence scoring** sees the signals **disagree** (0.80 vs 0.30). The
   combined `p(AI)` lands near the middle (~0.5), and the **disagreement penalty**
   drives confidence *down*. The verdict becomes **`uncertain`**, not
   `likely_ai`.
4. **Label** shown is the *uncertain* variant — it explicitly says the system
   can't tell and that this is not an accusation.
5. **Appeal** — if the poet still objects, they call `POST /appeal` with their
   reasoning; the content goes `under_review` and a human decides.

**Design consequences baked in from this trace:**
- **Disagreement lowers confidence** instead of averaging into a false verdict.
- **The `uncertain` band is wide** so borderline work lands there, not in `AI`.
- **AI labels are worded as "signs of AI," never "this is AI"** — hedged, never
  accusatory.
- **An appeal path always exists**, and appealed content is marked
  `under_review` so it's no longer presented as a settled verdict.
- We treat a false positive (human flagged as AI) as strictly worse than a false
  negative, and the thresholds are tuned toward that asymmetry.

---

## 4. API Surface (the contract)

| Method | Path         | Accepts                                   | Returns |
|--------|--------------|-------------------------------------------|---------|
| `POST` | `/submit`    | `{ "text": str, "creator_id"?: str }`     | `content_id`, `verdict`, `confidence`, `p_ai`, `signals` breakdown, `label` text |
| `POST` | `/appeal`    | `{ "content_id": str, "reason": str }`    | updated `status: "under_review"`, `appeal_id`, confirmation |
| `GET`  | `/log`       | optional `?limit=`                        | list of audit-log entries (decisions + appeals) |
| `GET`  | `/health`    | —                                         | `{ "status": "ok" }` liveness check |

**`POST /submit` response shape (draft):**
```json
{
  "content_id": "c_a1b2c3",
  "verdict": "uncertain",
  "confidence": 0.42,
  "p_ai": 0.51,
  "signals": {
    "stylometric": { "score": 0.80, "sentence_variance": 0.9, "ttr": 0.3, "punct_density": 0.1 },
    "llm":         { "score": 0.30, "rationale": "reads as human; distinctive voice" }
  },
  "label": {
    "variant": "uncertain",
    "text": "We couldn't confidently determine how this was created. Treat the attribution as inconclusive."
  }
}
```

**`POST /appeal` response shape (draft):**
```json
{
  "appeal_id": "a_9z8y7x",
  "content_id": "c_a1b2c3",
  "status": "under_review",
  "message": "Your appeal has been logged and this content is now under human review."
}
```

Errors: `400` (missing/invalid fields), `404` (`content_id` not found on appeal),
`429` (rate limit exceeded on `/submit`).

---

## Architecture

Two flows: submission and appeal. Arrows are labeled with what passes between
components.

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
                   │ style score s1∈[0,1]                  │ semantic score s2∈[0,1]
                   └───────────────┬────────────────────────┘
                                   ▼  (s1, s2)
                          [Confidence Scoring]
                                   │  combined p_ai + confidence
                                   │  (agreement ↑ conf, disagreement ↓ conf)
                                   ▼
                          [Transparency Label]
                                   │  verdict + confidence → label variant + text
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

## Checkpoint status (Milestone 1)

- [x] Can describe the full path of a submitted text, naming every component
      (§1).
- [x] Chose 2 distinct detection signals; documented what each captures and its
      blind spot (§2).
- [x] Traced the false-positive scenario through the system (§3).
- [x] Rough list of API endpoints defined (§4).
- [x] Diagram of both submission and appeal flows (§ Architecture).

## Next (Milestone 2 preview)
- Nail down the exact weighting for the combined `p_ai` and the
  agreement/disagreement confidence formula.
- Set the verdict thresholds (where `uncertain` starts and ends).
- Choose concrete rate-limit numbers and justify them.
- Write the three verbatim label variants for the README.
