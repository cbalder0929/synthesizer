/* -----------------------------------------------------------------
 * app.js -- browser-side controller for the additive synthesizer UI.
 *
 * Responsibilities:
 *   1. Build a clickable two-octave piano keyboard (MIDI 60-83) and
 *      append the corresponding number to the notes textarea on click.
 *   2. Load preset melodies into the textarea.
 *   3. Parse the form, POST it to /api/synthesize, and pipe the
 *      returned WAV bytes into the <audio> element + download link.
 *
 * The audio engine runs server-side in Python; this file does no DSP.
 * --------------------------------------------------------------- */

(() => {
  "use strict";

  // ----- DOM handles ----------------------------------------------
  const $notes      = document.getElementById("notes");
  const $bpm        = document.getElementById("bpm");
  const $durations  = document.getElementById("durations");
  const $piano      = document.getElementById("piano");
  const $synth      = document.getElementById("synthesize");
  const $status     = document.getElementById("status");
  const $player     = document.getElementById("player");
  const $download   = document.getElementById("download");
  const $clear      = document.getElementById("clear-notes");

  // ----- Piano keyboard -------------------------------------------
  //
  // We render two octaves starting at C4 (MIDI 60). The layout follows
  // the standard piano pattern of 7 white keys per octave with 5 black
  // keys interleaved. Black keys are positioned absolutely on top of
  // the white keys via CSS so the visual matches a real piano.
  //
  // White-key MIDI offsets within an octave: 0,2,4,5,7,9,11
  // Black-key MIDI offsets within an octave: 1,3,    6,8,10
  // (CC sharp positions per white-key index : 0,1, ,3,4,5  -> no after E, no after B)

  const WHITE_OFFSETS = [0, 2, 4, 5, 7, 9, 11];
  const BLACK_OFFSETS = [1, 3, 6, 8, 10];
  // Which white-key index a black key sits AFTER, within an octave.
  const BLACK_AFTER   = [0, 1, 3, 4, 5];
  const NOTE_NAMES    = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"];

  function buildKeyboard() {
    const startOctave = 4; // C4 = MIDI 60
    const octaves = 2;

    // First pass: white keys. We measure their layout to position blacks.
    const whiteKeys = [];
    for (let o = 0; o < octaves; o++) {
      for (let i = 0; i < WHITE_OFFSETS.length; i++) {
        const midi = 12 * (startOctave + o + 1) + WHITE_OFFSETS[i];
        const name = NOTE_NAMES[WHITE_OFFSETS[i]] + (startOctave + o);
        const key = document.createElement("button");
        key.type = "button";
        key.className = "key white";
        key.dataset.midi = midi;
        key.title = `${name} — MIDI ${midi}`;
        key.textContent = midi;
        $piano.appendChild(key);
        whiteKeys.push(key);
      }
    }

    // Second pass: black keys, absolutely positioned at the seam
    // between two white keys. We use the actual rendered geometry so
    // it works regardless of viewport width.
    const pianoRect = $piano.getBoundingClientRect();
    for (let o = 0; o < octaves; o++) {
      for (let j = 0; j < BLACK_OFFSETS.length; j++) {
        const whiteIdx = o * 7 + BLACK_AFTER[j];
        const left = whiteKeys[whiteIdx].getBoundingClientRect();
        const midi = 12 * (startOctave + o + 1) + BLACK_OFFSETS[j];
        const name = NOTE_NAMES[BLACK_OFFSETS[j]] + (startOctave + o);
        const key = document.createElement("button");
        key.type = "button";
        key.className = "key black";
        key.dataset.midi = midi;
        key.title = `${name} — MIDI ${midi}`;
        key.textContent = midi;
        // Position the 24-px wide black key so its center sits at the
        // boundary between this white key and the next.
        const centerX = left.right - pianoRect.left;
        key.style.left = (centerX - 12) + "px";
        key.style.top  = "8px";
        $piano.appendChild(key);
      }
    }

    // One delegated click handler appends the clicked key's MIDI
    // number to the notes textarea.
    $piano.addEventListener("click", (ev) => {
      const t = ev.target.closest(".key");
      if (!t) return;
      appendNote(t.dataset.midi);
    });
  }

  function appendNote(n) {
    const current = $notes.value.trim();
    $notes.value = current ? `${current}, ${n}` : `${n}`;
  }

  // ----- Preset melodies ------------------------------------------
  // Each preset is a tuple of (notes, bpm, durations). Durations may
  // be null to use the default (one beat per note).

  const PRESETS = {
    twinkle: {
      notes: [60,60,67,67,69,69,67, -1, 65,65,64,64,62,62,60],
      bpm: 110, durations: [1,1,1,1,1,1,2, 1, 1,1,1,1,1,1,2],
    },
    mary: {
      notes: [64,62,60,62,64,64,64, 62,62,62, 64,67,67],
      bpm: 120, durations: [1,1,1,1,1,1,2, 1,1,2, 1,1,2],
    },
    ode: {
      notes: [64,64,65,67,67,65,64,62,60,60,62,64,64,62,62],
      bpm: 110, durations: [1,1,1,1,1,1,1,1,1,1,1,1,1.5,0.5,2],
    },
    cmajor: {
      notes: [60,62,64,65,67,69,71,72],
      bpm: 140, durations: null,
    },
  };

  function loadPreset(name) {
    const p = PRESETS[name];
    if (!p) return;
    $notes.value     = p.notes.join(", ");
    $bpm.value       = p.bpm;
    $durations.value = p.durations ? p.durations.join(", ") : "";
  }

  document.querySelectorAll(".preset").forEach((btn) => {
    btn.addEventListener("click", () => loadPreset(btn.dataset.preset));
  });

  $clear.addEventListener("click", () => {
    $notes.value = "";
    $durations.value = "";
  });

  // ----- Form parsing & submission --------------------------------

  function parseIntList(raw) {
    return raw.split(",")
      .map((s) => s.trim())
      .filter(Boolean)
      .map((s) => {
        const n = Number(s);
        if (!Number.isInteger(n)) throw new Error(`"${s}" is not an integer.`);
        return n;
      });
  }

  function parseFloatList(raw) {
    return raw.split(",")
      .map((s) => s.trim())
      .filter(Boolean)
      .map((s) => {
        const n = Number(s);
        if (!Number.isFinite(n)) throw new Error(`"${s}" is not a number.`);
        return n;
      });
  }

  function setStatus(msg, kind) {
    $status.textContent = msg;
    $status.className = "status" + (kind ? " " + kind : "");
  }

  async function synthesize() {
    setStatus("");
    let notes, bpm, durations, timbre;
    try {
      notes = parseIntList($notes.value);
      if (!notes.length) throw new Error("Enter at least one MIDI note.");

      bpm = Number($bpm.value);
      if (!(bpm > 0 && bpm <= 1000)) {
        throw new Error("Tempo must be between 1 and 1000 BPM.");
      }

      const dRaw = $durations.value.trim();
      durations = dRaw ? parseFloatList(dRaw) : null;
      if (durations && durations.length !== notes.length) {
        throw new Error(
          `Got ${durations.length} durations for ${notes.length} notes.`
        );
      }

      timbre = document.querySelector('input[name="timbre"]:checked').value;
    } catch (err) {
      setStatus(err.message, "err");
      return;
    }

    $synth.disabled = true;
    setStatus("Synthesizing…");

    let res;
    try {
      res = await fetch("/api/synthesize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ notes, bpm, durations, timbre }),
      });
    } catch (err) {
      setStatus("Network error: " + err.message, "err");
      $synth.disabled = false;
      return;
    }

    if (!res.ok) {
      let msg = `Server error ${res.status}`;
      try {
        const j = await res.json();
        if (j && j.error) msg = j.error;
      } catch { /* body wasn't JSON */ }
      setStatus(msg, "err");
      $synth.disabled = false;
      return;
    }

    // The successful response body is a WAV blob. Wire it into the
    // <audio> element and expose a download link.
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    if ($player.dataset.objectUrl) {
      URL.revokeObjectURL($player.dataset.objectUrl);
    }
    $player.src = url;
    $player.dataset.objectUrl = url;
    $download.href = url;
    $download.hidden = false;

    setStatus(
      `Ready — ${notes.length} note${notes.length === 1 ? "" : "s"}, `
      + `${timbre}, ${bpm} BPM.`, "ok",
    );
    $synth.disabled = false;
    $player.play().catch(() => { /* autoplay may be blocked; ignore */ });
  }

  $synth.addEventListener("click", synthesize);

  // Ctrl/Cmd+Enter from the notes box triggers synthesis.
  $notes.addEventListener("keydown", (ev) => {
    if ((ev.ctrlKey || ev.metaKey) && ev.key === "Enter") synthesize();
  });

  // ----- Boot -----------------------------------------------------
  buildKeyboard();
  loadPreset("twinkle");
})();
