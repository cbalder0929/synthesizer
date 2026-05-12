# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install deps (Python 3, virtualenv recommended)
pip install -r requirements.txt

# Run the Flask web app — http://127.0.0.1:5000
python app.py

# Run the interactive CLI front-end
python synthesizer.py
```

There is no test suite, linter, or build step configured in this repo.

Quick smoke test against the running web app:

```bash
curl -sS -X POST http://127.0.0.1:5000/api/synthesize \
  -H "Content-Type: application/json" \
  -d '{"notes":[60,62,64,65,67],"bpm":120,"timbre":"triangle"}' \
  -o scale.wav
```

## Architecture

Two front-ends share **one** audio engine. The DSP lives in `synthesizer.py:Synthesizer` and must not be duplicated in the Flask layer.

- **`synthesizer.py`** — the `Synthesizer` class plus `prompt_*` helpers and a `main()` for the CLI. `sounddevice` (which wraps PortAudio) is imported **lazily inside `play()`** so the module is safe to import on hosts without PortAudio installed — the Flask app and any unit-test-like usage never trigger the import. Do not move it back to module top level: it will break Azure App Service and similar Linux containers.
- **`app.py`** — Flask wrapper. The only real logic is `_parse_payload()` (JSON validation → `ValueError` → HTTP 400) and `_audio_to_wav_bytes()` (in-memory WAV via `soundfile`). Every successful request constructs a fresh `Synthesizer(timbre=...)`, calls `melody()`, and streams the bytes back as `audio/wav`. There is no persistence or session state.
- **`templates/index.html` + `static/app.js` + `static/styles.css`** — single-page UI. `app.js` does **no DSP**; it builds a clickable two-octave keyboard (MIDI 60–83), loads preset melodies, POSTs `/api/synthesize`, and wires the response into an `<audio>` element and download link.

### Signal-flow inside `Synthesizer`

`melody()` → per-note `note()` → `oscillator()` × `envelope()` → concatenate → final peak-normalize to `self.amplitude`.

- `oscillator()` is **additive**: non-sine timbres sum sine harmonics with the textbook weights (sawtooth: all k, `(-1)^(k+1)/k`; square: odd k, `1/k`; triangle: odd k, `(-1)^((k-1)/2)/k²`) and bandlimit by skipping any harmonic `≥ Nyquist`. Each oscillator output is peak-normalized to `[-1, 1]` so timbres have comparable loudness before the envelope is applied.
- `envelope()` is a linear attack/decay (defaults 20 ms / 50 ms) that shrinks proportionally when a note is shorter than `attack + decay`, so notes always start and end at zero amplitude — important to avoid click artifacts on short notes.
- Negative MIDI numbers are **rests** (silence of the right length), not errors. This convention is enforced at all three layers: `prompt_notes()`, `_parse_payload()`, and `Synthesizer.note()`. Range check is `-MIDI_MAX … MIDI_MAX` (i.e. ±127).

### Invariants worth preserving

- Per-note durations are in **beats**, not seconds; conversion goes through `bpm2sec()`. The `durations` list (when supplied) must match `len(notes)` exactly — both the CLI prompt and the API enforce this.
- Sample rate is fixed at 44.1 kHz mono; WAV output is 16-bit PCM. If you change the sample rate, the Nyquist cutoff in `oscillator()` follows automatically (`self.sample_rate / 2.0`).
- Valid timbres are listed in **three** places that must stay in sync: `Synthesizer.__init__` validation, `app.VALID_TIMBRES`, and the `<select>` in `templates/index.html` (plus `prompt_timbre()` for the CLI). Adding a new waveform means touching all of them.
