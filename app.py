"""
app.py
======

Flask front-end for the monophonic additive synthesizer in
``synthesizer.py``. The audio engine itself is untouched: this module
only adds a thin HTTP layer that takes JSON requests from the browser,
calls ``Synthesizer.melody(...)``, and streams the result back as a
16-bit PCM WAV file.

Run with::

    python app.py

then open http://127.0.0.1:5000 in a browser.
"""

from __future__ import annotations

import io
import logging

import numpy as np
import soundfile as sf
from flask import Flask, jsonify, render_template, request, send_file

from synthesizer import Synthesizer


# ---------------------------------------------------------------------------
# Flask app & logging
# ---------------------------------------------------------------------------

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = app.logger


VALID_TIMBRES = ("sine", "triangle", "sawtooth", "square")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_payload(data: dict) -> tuple[list[int], float, list[float] | None, str]:
    """
    Validate the JSON payload from the browser and return a normalized
    tuple ``(notes, bpm, durations, timbre)``.

    Raises ``ValueError`` with a user-facing message on any problem,
    which the route handler turns into HTTP 400.
    """
    if not isinstance(data, dict):
        raise ValueError("Request body must be a JSON object.")

    # ----- notes -----------------------------------------------------------
    raw_notes = data.get("notes")
    if not isinstance(raw_notes, list) or not raw_notes:
        raise ValueError("`notes` must be a non-empty list of integers.")
    try:
        notes = [int(n) for n in raw_notes]
    except (TypeError, ValueError):
        raise ValueError("Every entry in `notes` must be an integer.")
    bad = [n for n in notes if n > Synthesizer.MIDI_MAX or n < -Synthesizer.MIDI_MAX]
    if bad:
        raise ValueError(
            f"These MIDI values are out of range: {bad}. "
            "Use 0-127 for notes, or any negative number for a rest."
        )

    # ----- bpm -------------------------------------------------------------
    try:
        bpm = float(data.get("bpm"))
    except (TypeError, ValueError):
        raise ValueError("`bpm` must be a number.")
    if bpm <= 0 or bpm > 1000:
        raise ValueError("`bpm` must lie in (0, 1000].")

    # ----- durations -------------------------------------------------------
    raw_durations = data.get("durations")
    durations: list[float] | None
    if raw_durations in (None, [], ""):
        durations = None
    elif isinstance(raw_durations, list):
        try:
            durations = [float(b) for b in raw_durations]
        except (TypeError, ValueError):
            raise ValueError("Every entry in `durations` must be a number.")
        if len(durations) != len(notes):
            raise ValueError(
                f"Got {len(durations)} durations for {len(notes)} notes; "
                "the two lists must be the same length."
            )
        if any(b <= 0 for b in durations):
            raise ValueError("Each duration must be greater than zero beats.")
    else:
        raise ValueError("`durations` must be a list or omitted.")

    # ----- timbre ----------------------------------------------------------
    timbre = data.get("timbre", "triangle")
    if timbre not in VALID_TIMBRES:
        raise ValueError(
            f"`timbre` must be one of {VALID_TIMBRES}, got {timbre!r}."
        )

    return notes, bpm, durations, timbre


def _audio_to_wav_bytes(audio: np.ndarray, sample_rate: int) -> io.BytesIO:
    """Encode a float audio buffer to a 16-bit PCM WAV in memory."""
    buf = io.BytesIO()
    sf.write(buf, audio, sample_rate, subtype="PCM_16", format="WAV")
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Serve the single-page UI."""
    return render_template("index.html")


@app.route("/api/synthesize", methods=["POST"])
def synthesize():
    """
    Synthesize a melody from a JSON payload and return a WAV file.

    Request JSON::

        {
            "notes":     [60, 62, 64, ...],   // ints, negative = rest
            "bpm":       120,                  // > 0 and <= 1000
            "durations": [1, 1, 0.5, ...],     // optional, same length as notes
            "timbre":    "triangle"            // sine|triangle|sawtooth|square
        }

    Response: ``audio/wav`` body (16-bit PCM), or JSON ``{error}`` on 400.
    """
    try:
        payload = request.get_json(force=True, silent=False)
    except Exception:
        return jsonify(error="Request body must be valid JSON."), 400

    try:
        notes, bpm, durations, timbre = _parse_payload(payload)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    log.info(
        "Synthesizing %d notes at %.1f BPM, timbre=%s, custom_durations=%s",
        len(notes), bpm, timbre, durations is not None,
    )

    synth = Synthesizer(timbre=timbre)
    try:
        audio = synth.melody(notes, bpm, durations)
    except Exception as exc:  # defensive: shouldn't happen post-validation
        log.exception("Synthesis failed")
        return jsonify(error=f"Synthesis failed: {exc}"), 500

    wav = _audio_to_wav_bytes(audio, synth.sample_rate)
    return send_file(
        wav,
        mimetype="audio/wav",
        as_attachment=False,
        download_name="melody.wav",
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
