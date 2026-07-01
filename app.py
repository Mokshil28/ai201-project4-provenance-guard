"""Provenance Guard — Flask API.

Milestone 3: submission endpoint + Signal 1 (stylometric) wired end-to-end,
structured audit logging, and a /log view.

Confidence and the transparency label are PLACEHOLDERS here — the real
two-signal confidence scoring arrives in Milestone 4 and the label variants in
Milestone 5.
"""

import uuid
from datetime import datetime, timezone

from flask import Flask, jsonify, request

import storage
from signals import stylometric_score

app = Flask(__name__)
storage.init_db()


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _preliminary_attribution(score):
    """Single-signal placeholder verdict. Superseded by real scoring in M4."""
    if score >= 0.65:
        return "likely_ai"
    if score <= 0.45:
        return "likely_human"
    return "uncertain"


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

    # --- Signal 1: stylometric -------------------------------------------
    stylo = stylometric_score(text)
    s_stylo = stylo["score"]

    attribution = _preliminary_attribution(s_stylo)
    # PLACEHOLDER confidence/label — finalized in M4/M5.
    confidence = round(abs(s_stylo - 0.5) * 2, 3)
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
        "p_ai": s_stylo,  # single-signal stand-in until M4 blends in the LLM
        "status": "classified",
        "signals": {"stylometric": stylo},
        "label": label,
        "note": "confidence and label are placeholders (M3); real scoring in M4/M5",
    }

    storage.save_submission(record)

    # Structured audit entry for this decision.
    storage.add_audit(
        "decision",
        content_id,
        {
            "content_id": content_id,
            "creator_id": creator_id,
            "timestamp": timestamp,
            "attribution": attribution,
            "confidence": confidence,
            "stylometric_score": s_stylo,
            "signals": {"stylometric": stylo},
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
