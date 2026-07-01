"""Provenance Guard — Flask API.

Milestone 4: both detection signals (stylometric + LLM) wired end-to-end with
real two-signal confidence scoring, and an audit log that records each signal's
individual score alongside the combined result.

The transparency label text is still a PLACEHOLDER — the three label variants
arrive in Milestone 5. The `verdict` (likely_ai / likely_human / uncertain) is
real as of this milestone.
"""

import uuid
from datetime import datetime, timezone

from flask import Flask, jsonify, request

import scoring
import storage
from signals import MIN_WORDS_FOR_STYLOMETRY, llm_score, stylometric_score

app = Flask(__name__)
storage.init_db()


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/submit")
def submit():
    body = request.get_json(silent=True) or {}
    text = body.get("text")
    creator_id = body.get("creator_id")

    if not text or not isinstance(text, str) or not text.strip():
        return jsonify({"error": "field 'text' is required and must be a non-empty string"}), 400

    content_id = "c_" + uuid.uuid4().hex[:12]
    timestamp = _now_iso()

    # --- Signal 1: stylometric (structural) ------------------------------
    stylo = stylometric_score(text)
    s_stylo = stylo["score"]

    # --- Signal 2: LLM classifier (semantic) -----------------------------
    llm = llm_score(text)
    s_llm = llm["score"]  # None if the LLM was unavailable -> graceful fallback

    # --- Confidence scoring: combine both signals ------------------------
    short_text = stylo["word_count"] < MIN_WORDS_FOR_STYLOMETRY
    scored = scoring.combine(s_stylo, s_llm, short_text=short_text)

    attribution = scored["verdict"]
    confidence = scored["confidence"]

    # PLACEHOLDER label text — the three real variants arrive in Milestone 5.
    label = {
        "variant": attribution,
        "text": "[placeholder label — finalized in Milestone 5]",
    }

    record = {
        "content_id": content_id,
        "creator_id": creator_id,
        "timestamp": timestamp,
        "text": text,
        "attribution": attribution,
        "confidence": confidence,
        "confidence_band": scored["confidence_band"],
        "p_ai": scored["p_ai"],
        "agreement": scored["agreement"],
        "scoring_mode": scored["mode"],
        "status": "classified",
        "signals": {"stylometric": stylo, "llm": llm},
        "label": label,
        "note": "label text is a placeholder (M4); real label variants in M5",
    }

    storage.save_submission(record)

    # Structured audit entry — records BOTH signals + the combined result.
    storage.add_audit(
        "decision",
        content_id,
        {
            "content_id": content_id,
            "creator_id": creator_id,
            "timestamp": timestamp,
            "attribution": attribution,
            "confidence": confidence,
            "confidence_band": scored["confidence_band"],
            "p_ai": scored["p_ai"],
            "agreement": scored["agreement"],
            "scoring_mode": scored["mode"],
            "stylometric_score": s_stylo,
            "llm_score": s_llm,
            "signals": {"stylometric": stylo, "llm": llm},
            "status": "classified",
        },
    )

    # API response (omit stored full text).
    response = {k: v for k, v in record.items() if k != "text"}
    return jsonify(response), 200


@app.get("/log")
def log():
    limit = request.args.get("limit", default=50, type=int)
    return jsonify({"entries": storage.get_log(limit)})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
