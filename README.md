# 🎵 Additive Synthesizer

> **What is this?** A program that turns a list of musical notes into a
> real audio file — right in your browser or from the command line.
> No piano, no DAW, no music theory degree required.

---

## Why this was built

This project started as a way to learn how computers actually *make*
sound from scratch. Most music apps hide all the math behind knobs and
buttons. This one exposes the engine so you can see (and hear) exactly
what is going on. It is also a handy tool for quickly turning a melody
idea into a playable WAV file without opening any heavy software.

---

## What it does

You give it:
- A list of **notes** (numbers from 0 to 127 — more on that below)
- A **tempo** (how fast, in beats per minute)
- An optional **timbre** (what kind of sound — sine, triangle, sawtooth, or square)

It gives you back:
- A **.wav audio file** you can play or download immediately

There are two ways to use it:

| Mode | Best for | How |
|------|----------|-----|
| **Web app** | Anyone — no coding needed | Open it in a browser, click buttons |
| **CLI (command line)** | Developers / power users | Run `python synthesizer.py` in a terminal |

---

## How it was built

The project is written entirely in **Python** and uses a small set of
well-known libraries:

| Library | What it does |
|---------|-------------|
| `numpy` | Number crunching — turns math formulas into thousands of audio samples per second |
| `soundfile` | Writes the samples to a `.wav` file |
| `sounddevice` | Plays audio directly through your speakers (CLI only) |
| `flask` | Runs the tiny web server that powers the browser UI |

There are no paid APIs, no AI models, and no external services. All the
audio is generated locally on your machine with pure math.

### The two parts of the code

```
synthesizer.py   ← the audio engine (all the maths live here)
app.py           ← the web server that wraps the engine
```

The web UI (`templates/index.html` + `static/app.js`) talks to `app.py`
over a simple HTTP request. `app.py` calls `synthesizer.py`. This means
the actual sound-making code is written once and shared by both the
browser and the command-line versions.

---

## How the sound is actually made (plain English)

Every sound you hear is a wave — a repeating pattern of air pressure
changes. A pure tone is a simple smooth wave (called a **sine wave**).
More complex sounds are built by adding many sine waves together. This
technique is called **additive synthesis**, which is where the project
gets its name.

Here is the step-by-step process for each note:

1. **Convert the note number to a frequency.**
   MIDI note 69 = 440 Hz (concert A). Note 60 = 261 Hz (middle C).
   Each step up or down changes the pitch by one semitone.

2. **Build the wave by adding harmonics.**
   Depending on the timbre you chose, the program sums a series of
   sine waves at whole-number multiples of the base frequency:
   - **Sine** — just the base frequency. Pure and clean.
   - **Triangle** — adds a few odd harmonics, very quietly. Soft and mellow.
   - **Square** — adds odd harmonics at medium strength. Hollow, retro sound.
   - **Sawtooth** — adds every harmonic at full strength. Bright and buzzy.

3. **Apply an envelope.**
   A quick 20 ms fade-in (attack) and 50 ms fade-out (decay) are
   applied so each note starts and ends smoothly with no click or pop.

4. **String the notes together and normalize.**
   All the note waves are joined into one long sequence. The whole thing
   is scaled so the loudest point is just under the maximum volume —
   this prevents distortion.

5. **Write to WAV.**
   The final wave is saved as a standard 44 100 Hz, 16-bit, mono WAV
   file — the same format used by CDs.

---

## Quick start (5 minutes)

### Prerequisites

- Python 3.9 or newer ([download](https://www.python.org/downloads/))
- A terminal (Command Prompt / PowerShell on Windows, Terminal on
  macOS/Linux)

### Install

```bash
# 1. (recommended) create a virtual environment so packages stay isolated
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

# 2. install the dependencies
pip install -r requirements.txt
```

### Run the web app

```bash
python app.py
```

Then open **http://127.0.0.1:5000** in your browser. You should see a
piano keyboard and some controls.

### Run the command-line version

```bash
python synthesizer.py
```

Follow the prompts to enter notes, tempo, and timbre. The program will
play the melody through your speakers and save a `.wav` file.

---

## Using the web UI

1. **Enter notes** — type comma-separated numbers in the **Notes** box,
   or click the on-screen piano keys to build up a melody one note at a
   time. `60` is middle C, `69` is concert A (440 Hz). Any **negative**
   number is a rest (silence) of the same length.
2. **Use a preset** — click *Twinkle Twinkle*, *Mary Had a Little Lamb*,
   *Ode to Joy*, or *C Major Scale* to fill in the notes and durations
   automatically.
3. **Set the tempo** — enter a BPM value. 120 is a typical pop song speed.
4. **Set durations (optional)** — enter a comma-separated list of beat
   lengths, one per note (e.g. `1, 1, 0.5, 0.5, 2`). Leave blank to
   make every note one beat long.
5. **Pick a timbre** — choose how the notes sound from the drop-down.
6. **Hit Synthesize** (or press `Ctrl+Enter`) — the WAV is generated
   instantly, plays in the audio player, and a **Download WAV** button
   appears.

---

## HTTP API (for developers)

The web UI is just a client for one endpoint. You can call it directly
from code or with `curl`.

### `POST /api/synthesize`

**Request body (JSON):**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `notes` | `int[]` | ✅ | Note numbers 0–127; negative = rest |
| `bpm` | `number` | ✅ | Tempo, must be between 0 and 1000 |
| `durations` | `number[]` | ❌ | Beat lengths per note; defaults to 1 each |
| `timbre` | `string` | ❌ | `"sine"`, `"triangle"` (default), `"sawtooth"`, `"square"` |

**Response:** an `audio/wav` file on success, or a JSON error message on
failure.

**Example:**

```bash
curl -sS -X POST http://127.0.0.1:5000/api/synthesize \
  -H "Content-Type: application/json" \
  -d '{"notes":[60,62,64,65,67],"bpm":120,"timbre":"triangle"}' \
  -o scale.wav
```

---

## Files in this project

```
synthesizer/
├── app.py              # Web server — receives browser requests and calls the engine
├── synthesizer.py      # Audio engine — all the maths that generate sound
├── requirements.txt    # Python packages needed to run the project
├── README.md           # This file
├── templates/
│   └── index.html      # The browser UI (the page you see at localhost:5000)
└── static/
    ├── styles.css       # Visual styling — dark theme, piano keys layout
    └── app.js           # Browser-side logic — keyboard, presets, audio player
```

---

## Troubleshooting

**`OSError: PortAudio library not found`** (CLI only)
> PortAudio is the low-level library that lets Python talk to your
> speakers. Install it with:
> - Linux: `sudo apt install libportaudio2`
> - macOS: `brew install portaudio`
>
> Or just use the web app — it never needs a sound device on the server
> because it sends the WAV file to your browser instead.

**The audio player doesn't auto-play**
> Browsers block audio from playing until you interact with the page
> first (a security rule). Click play once manually and it will
> auto-play on the next synthesis.

**`400 Bad Request` error**
> The error message will say which field is wrong. The most common
> causes are:
> - The `durations` list has a different number of items than `notes`
> - A MIDI note number is greater than 127 or less than −127
> - An unknown timbre name was typed in

---

## License

Provided as-is for educational use.
