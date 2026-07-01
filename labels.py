"""Transparency-label generation.

Maps a verdict + confidence to the exact reader-facing label text from
planning.md §3. Three variants, one per verdict; the numeric confidence and the
band word (HIGH / MODERATE / LOW) are interpolated so the text changes with the
score. AI wording is hedged and never accusatory, because falsely flagging a
human as AI is the worse error.
"""

from scoring import confidence_band

_TEMPLATES = {
    "likely_ai": (
        "⚠️ Likely AI-generated. Our automated analysis found signs "
        "this text may have been produced with AI assistance (confidence: "
        "{pct}% — {band}). This is an estimate, not a certainty, and it is "
        "not an accusation. If you're the creator and this is wrong, you can "
        "appeal and a human will review it."
    ),
    "likely_human": (
        "✓ Likely human-written. Our automated analysis found signs this "
        "text was written by a person (confidence: {pct}% — {band}). This "
        "is an automated estimate, not a guarantee of authorship."
    ),
    "uncertain": (
        "❔ Attribution uncertain. Our automated analysis couldn't "
        "confidently determine whether this text was written by a person or "
        "generated with AI (confidence: {pct}% — {band}). Please treat the "
        "origin as inconclusive. No attribution claim is being made."
    ),
}


def make_label(verdict, confidence):
    """Return {variant, text} for the given verdict and confidence score."""
    template = _TEMPLATES.get(verdict, _TEMPLATES["uncertain"])
    pct = round(confidence * 100)
    band = confidence_band(confidence)
    return {"variant": verdict, "text": template.format(pct=pct, band=band)}
