"""Confidence scoring — combine the two signals into p_ai + confidence + verdict.

Implements the formulas and thresholds from planning.md §1-§2 verbatim:

    p_ai       = 0.65 * s_llm + 0.35 * s_stylo   # LLM leads (stronger signal)
    disagree   = |s_llm - s_stylo|
    raw_conf   = 2 * |p_ai - 0.5|                 # distance from the midpoint
    confidence = raw_conf * (1 - 0.5 * disagree)  # disagreement halves conf at most

Disagreement lowers confidence (halving it at the extreme) rather than zeroing
it, so a confident LLM isn't fully neutralized by a lukewarm stylometric signal.
False-positive protection comes primarily from the asymmetric verdict
thresholds: a higher bar to call AI than to call human, because flagging a human
as AI is the worse error.
"""

WEIGHT_LLM = 0.65
WEIGHT_STYLO = 0.35
# How much total signal disagreement can cut confidence (0.5 => halve at most).
DISAGREEMENT_PENALTY = 0.5

# A lone stylometric signal can never reach the AI verdict (needs conf >= 0.50),
# so it can never falsely flag AI on its own. Kept below that threshold on purpose.
SINGLE_SIGNAL_CONF_CAP = 0.45
# Too-short text: cap confidence into the LOW band so we never over-claim.
SHORT_TEXT_CONF_CAP = 0.34


def confidence_band(confidence):
    if confidence >= 0.65:
        return "HIGH"
    if confidence >= 0.35:
        return "MODERATE"
    return "LOW"


def _verdict(p_ai, confidence):
    if p_ai <= 0.45 and confidence >= 0.35:
        return "likely_human"
    if p_ai >= 0.65 and confidence >= 0.50:   # stricter both ways
        return "likely_ai"
    return "uncertain"


def combine(s_stylo, s_llm, short_text=False):
    """Blend the two signal scores into a calibrated result.

    s_llm may be None (LLM unavailable) -> fall back to stylometric only, with
    confidence capped so a single signal can't yield a confident verdict.
    """
    if s_llm is None:
        p_ai = s_stylo
        agreement = None
        raw_conf = 2 * abs(p_ai - 0.5)
        confidence = min(raw_conf, SINGLE_SIGNAL_CONF_CAP)
        mode = "stylometric_only"
    else:
        p_ai = WEIGHT_LLM * s_llm + WEIGHT_STYLO * s_stylo
        disagreement = abs(s_llm - s_stylo)
        agreement = 1 - disagreement
        raw_conf = 2 * abs(p_ai - 0.5)
        confidence = raw_conf * (1 - DISAGREEMENT_PENALTY * disagreement)
        mode = "two_signal"

    if short_text:
        confidence = min(confidence, SHORT_TEXT_CONF_CAP)

    confidence = round(max(0.0, min(1.0, confidence)), 3)
    verdict = _verdict(p_ai, confidence)

    return {
        "p_ai": round(p_ai, 3),
        "confidence": confidence,
        "confidence_band": confidence_band(confidence),
        "verdict": verdict,
        "agreement": round(agreement, 3) if agreement is not None else None,
        "mode": mode,
    }
