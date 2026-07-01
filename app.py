"""Provenance Guard — Flask API.

Milestone 5 (production layer): the full pipeline plus the four production
features — transparency labels that vary by confidence, an appeals workflow,
rate limiting on /submit, and a complete structured audit log covering both
decisions and appeals.

Endpoints:
  POST /submit   -> classify text (rate limited)
  POST /appeal   -> contest a classification; sets status to under_review
  GET  /appeals  -> reviewer queue (submissions under review + their appeals)
  GET  /log      -> structured audit log (decisions + appeals)
  GET  /health   -> liveness
"""

import uuid
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import scoring
import storage
from labels import make_label
from signals import MIN_WORDS_FOR_STYLOMETRY, llm_score, stylometric_score

app = Flask(__name__)
storage.init_db()

# Rate limiting — see README for the chosen limits and reasoning.
SUBMIT_RATE_LIMITS = "10 per minute;100 per day"
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@app.errorhandler(429)
def ratelimit_handler(exc):
    return (
        jsonify(
            {
                "error": "rate limit exceeded",
                "limit": SUBMIT_RATE_LIMITS,
                "detail": str(exc.description),
            }
        ),
        429,
    )


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/submit")
@limiter.limit(SUBMIT_RATE_LIMITS)
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

    # Transparency label — varies with verdict and confidence (see labels.py).
    label = make_label(attribution, confidence)

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
        "appealed": False,
        "signals": {"stylometric": stylo, "llm": llm},
        "label": label,
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
            "appealed": False,
        },
    )

    # API response (omit stored full text).
    response = {k: v for k, v in record.items() if k != "text"}
    return jsonify(response), 200


@app.post("/appeal")
def appeal():
    body = request.get_json(silent=True) or {}
    content_id = body.get("content_id")
    # accept either creator_reasoning (project spec) or reason (alias)
    reasoning = body.get("creator_reasoning") or body.get("reason")

    if not content_id:
        return jsonify({"error": "field 'content_id' is required"}), 400
    if not reasoning or not str(reasoning).strip():
        return jsonify({"error": "field 'creator_reasoning' is required"}), 400

    original = storage.get_submission(content_id)
    if original is None:
        return jsonify({"error": f"no submission found for content_id '{content_id}'"}), 404

    appeal_id = "a_" + uuid.uuid4().hex[:12]
    timestamp = _now_iso()

    storage.save_appeal(
        {
            "appeal_id": appeal_id,
            "content_id": content_id,
            "reason": reasoning,
            "created_at": timestamp,
        }
    )
    storage.update_status(content_id, "under_review")

    # Log the appeal ALONGSIDE the original decision — nothing is overwritten.
    storage.add_audit(
        "appeal",
        content_id,
        {
            "appeal_id": appeal_id,
            "content_id": content_id,
            "timestamp": timestamp,
            "appeal_reasoning": reasoning,
            "status": "under_review",
            "original_decision": {
                "attribution": original.get("attribution"),
                "confidence": original.get("confidence"),
                "p_ai": original.get("p_ai"),
                "stylometric_score": original.get("signals", {})
                .get("stylometric", {})
                .get("score"),
                "llm_score": original.get("signals", {}).get("llm", {}).get("score"),
            },
        },
    )

    return (
        jsonify(
            {
                "appeal_id": appeal_id,
                "content_id": content_id,
                "status": "under_review",
                "message": "Your appeal has been logged and this content is now "
                "under human review.",
            }
        ),
        200,
    )


@app.get("/appeals")
def appeals_queue():
    """Reviewer view: every submission currently under review, with its appeal."""
    queue = []
    for entry in storage.get_log(limit=500):
        if entry.get("entry_type") == "appeal":
            original = storage.get_submission(entry["content_id"]) or {}
            queue.append(
                {
                    "appeal_id": entry.get("appeal_id"),
                    "content_id": entry.get("content_id"),
                    "status": original.get("status", "under_review"),
                    "appeal_reasoning": entry.get("appeal_reasoning"),
                    "original_decision": entry.get("original_decision"),
                    "appealed_at": entry.get("timestamp"),
                }
            )
    return jsonify({"under_review": queue, "count": len(queue)})


@app.get("/log")
def log():
    limit = request.args.get("limit", default=50, type=int)
    return jsonify({"entries": storage.get_log(limit)})


if __name__ == "__main__":
    # use_reloader=False: the reloader spawns a second process, which would keep
    # a separate in-memory rate-limit counter. One process keeps limits accurate.
    app.run(host="127.0.0.1", port=5000, debug=True, use_reloader=False)
