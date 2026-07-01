"""Detection signals for Provenance Guard.

Signal 1 (this milestone): stylometric heuristics — a deterministic, pure-Python
structural measure. Higher score = more AI-like.

Signal 2 (LLM / Groq) is added in Milestone 4.
"""

import re

# Below this word count, stylometric statistics (variance, TTR) are unstable.
MIN_WORDS_FOR_STYLOMETRY = 40

_SENTENCE_SPLIT = re.compile(r"[.!?]+")
_WORD_RE = re.compile(r"[A-Za-z']+")
# Punctuation-mark *types* a human writer might reach for.
_PUNCT_MARKS = ".,;:!?\"'()[]{}—–-…"


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs):
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5


def stylometric_score(text):
    """Score the structural 'shape' of `text`.

    Returns a dict with the combined AI-likeness `score` in [0, 1] (higher =
    more AI-like) plus the raw sub-metrics, for the audit log and debugging.

    Each sub-metric maps monotonically to an AI-likeness contribution:
      - low sentence-length variation  -> AI-like (uniform)
      - low type-token ratio           -> AI-like (repetitive vocabulary)
      - few distinct punctuation types  -> AI-like (regular)
    """
    text = text or ""
    words = _WORD_RE.findall(text.lower())
    word_count = len(words)

    if word_count == 0:
        return {
            "score": 0.5,
            "sentence_variance": 0.0,
            "ttr": 0.0,
            "punct_density": 0.0,
            "punct_variety": 0,
            "word_count": 0,
            "note": "no analyzable words; stylometry unreliable",
        }

    # --- Sub-metric 1: sentence-length uniformity ---------------------------
    sentences = [s for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    sent_lengths = [len(_WORD_RE.findall(s)) for s in sentences]
    sent_lengths = [n for n in sent_lengths if n > 0]
    mean_len = _mean(sent_lengths)
    # Coefficient of variation: std relative to mean. Low CV = uniform = AI-like.
    cv = (_std(sent_lengths) / mean_len) if mean_len > 0 else 0.0
    ai_variance = _clamp(1.0 - cv)

    # --- Sub-metric 2: type-token ratio (lexical diversity) ----------------
    ttr = len(set(words)) / word_count
    # Map TTR onto [0,1]: TTR<=0.3 very repetitive -> AI; TTR>=0.7 diverse -> human.
    ai_ttr = _clamp((0.7 - ttr) / 0.4)

    # --- Sub-metric 3: punctuation regularity ------------------------------
    punct_chars = [c for c in text if c in _PUNCT_MARKS]
    punct_density = len(punct_chars) / word_count
    punct_variety = len(set(punct_chars))
    # Few distinct mark types -> regular -> AI-like. 5+ types reads as human.
    ai_punct = _clamp(1.0 - punct_variety / 5.0)

    score = _mean([ai_variance, ai_ttr, ai_punct])

    result = {
        "score": round(score, 3),
        "sentence_variance": round(cv, 3),
        "ttr": round(ttr, 3),
        "punct_density": round(punct_density, 3),
        "punct_variety": punct_variety,
        "word_count": word_count,
    }
    if word_count < MIN_WORDS_FOR_STYLOMETRY:
        result["note"] = "text too short for reliable stylometry"
    return result
