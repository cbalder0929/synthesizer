"""
synthesizer.py
==============

A monophonic additive synthesizer in Python.

The user is prompted to:
    1. Enter a melody as a comma-separated list of MIDI note numbers.
       Negative numbers are interpreted as rests (bonus feature #1).
    2. Enter the tempo in BPM (beats per minute).
    3. Optionally, enter a list of per-note durations in beats
       (bonus feature #2). If left blank, every note lasts one beat.
    4. Choose a timbre preset: sine, triangle, sawtooth, or square
       (bonus feature #3). Each non-sine preset is built by
       additive synthesis from a sum of bandlimited harmonics.
    5. Decide whether to play the synthesized melody through the
       sound card (sounddevice).
    6. Decide whether to write the synthesized melody to a .wav file
       (soundfile).

The synthesizer is wrapped in a `Synthesizer` class (bonus feature #4)
so that its parameters (sample rate, amplitude, attack/decay times,
timbre) are kept together and the functions act as methods.

Defensive input parsing is used throughout. Out-of-range MIDI numbers,
non-numeric input, empty input, zero or negative tempo, mismatched
duration lists, etc., all produce a clear warning rather than a crash.

Modules used (all introduced in class):
    - numpy           : numerical array operations and waveform generation
    - sounddevice     : real-time audio playback
    - soundfile       : reading/writing WAV files
    - sys, os         : minor utilities for output paths and exit
"""

import os
import sys

import numpy as np
import soundfile as sf

# Note: `sounddevice` is imported lazily inside Synthesizer.play() so that
# the rest of the engine (and the Flask web app, which never plays audio
# server-side) can run on hosts that don't ship PortAudio -- e.g. Azure
# App Service Linux containers. Installing libportaudio2 there is fiddly
# and unnecessary for the web use case.


# ---------------------------------------------------------------------------
# The Synthesizer class
# ---------------------------------------------------------------------------

class Synthesizer:
    """
    A monophonic additive synthesizer.

    Parameters
    ----------
    sample_rate : int
        Sample rate in Hz. 44100 is the CD-quality default.
    amplitude : float
        Peak amplitude for each note, in the range (0, 1]. The final
        melody is normalized so its absolute peak equals this value,
        which keeps the output well below clipping at 0 dBFS.
    attack : float
        Fade-in time in seconds for each note's envelope.
    decay : float
        Fade-out time in seconds for each note's envelope.
    timbre : str
        One of {"sine", "triangle", "sawtooth", "square"}. Sets the
        waveform used by `oscillator()`.
    num_harmonics : int
        Maximum number of harmonics summed in additive synthesis for
        bandlimited waveforms. The synthesizer will internally cap
        this so that no harmonic exceeds the Nyquist frequency.

    Notes
    -----
    All non-sine waveforms are synthesized additively, by summing
    sine harmonics with the proper amplitude/phase relationships:

        - sawtooth :  sum_{k=1..K}  (-1)^(k+1) * sin(2*pi*k*f*t) / k
        - square   :  sum_{k=1,3,5..} sin(2*pi*k*f*t) / k
        - triangle :  sum_{k=1,3,5..} (-1)^((k-1)/2) * sin(2*pi*k*f*t) / k**2

    Each harmonic is only added if k * f < sample_rate / 2 so that the
    result is strictly bandlimited (i.e., free of aliasing). This is
    the standard textbook formulation of additive synthesis.
    """

    # ----- MIDI / time conversions -----------------------------------------

    # MIDI specifies 128 note numbers (0..127). The conversion follows
    # the standard formula f = 440 * 2 ** ((n - 69) / 12), where n = 69
    # is concert pitch A4 = 440 Hz.
    MIDI_MIN = 0
    MIDI_MAX = 127

    def __init__(
        self,
        sample_rate=44100,
        amplitude=0.8,
        attack=0.02,
        decay=0.05,
        timbre="triangle",
        num_harmonics=40,
    ):
        # Basic sanity checks on construction parameters.
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive.")
        if not (0 < amplitude <= 1):
            raise ValueError("amplitude must lie in the interval (0, 1].")
        if attack < 0 or decay < 0:
            raise ValueError("attack and decay must be non-negative.")
        if num_harmonics < 1:
            raise ValueError("num_harmonics must be at least 1.")
        if timbre not in ("sine", "triangle", "sawtooth", "square"):
            raise ValueError(
                f"Unknown timbre {timbre!r}. "
                "Choose from sine, triangle, sawtooth, square."
            )

        self.sample_rate = int(sample_rate)
        self.amplitude = float(amplitude)
        self.attack = float(attack)
        self.decay = float(decay)
        self.timbre = timbre
        self.num_harmonics = int(num_harmonics)

    # ----- Unit conversions -----------------------------------------------

    def midi2freq(self, midi_note):
        """
        Convert a MIDI note number to its frequency in Hz.

        Parameters
        ----------
        midi_note : int
            MIDI note number in the range [0, 127].

        Returns
        -------
        float
            Frequency in Hz. MIDI 69 (A4) returns 440.0 Hz.
        """
        return 440.0 * 2.0 ** ((midi_note - 69) / 12.0)

    def bpm2sec(self, bpm, beats=1.0):
        """
        Convert a tempo in BPM to a duration in seconds.

        Parameters
        ----------
        bpm : float
            Tempo in beats per minute.
        beats : float
            Number of beats the note (or rest) should last.

        Returns
        -------
        float
            Duration in seconds.
        """
        return 60.0 * beats / bpm

    def sec2samples(self, seconds):
        """
        Convert a duration in seconds to an integer number of samples.

        Always rounds to a non-negative integer; a zero-length result
        is allowed (the caller may treat it as a no-op).
        """
        return max(0, int(round(seconds * self.sample_rate)))

    # ----- Oscillator -----------------------------------------------------

    def oscillator(self, frequency, n_samples):
        """
        Generate an oscillator waveform of the requested timbre.

        For non-sine timbres the waveform is built by additive synthesis:
        a sum of sine harmonics with the appropriate amplitude weights
        and phases. Each harmonic is included only if it lies below the
        Nyquist frequency, so the output is bandlimited (alias-free).

        Parameters
        ----------
        frequency : float
            Fundamental frequency in Hz.
        n_samples : int
            Number of output samples.

        Returns
        -------
        numpy.ndarray
            1-D float64 array of length n_samples, peak-normalized to
            the range [-1, 1] when the timbre would otherwise exceed it.
        """
        if n_samples <= 0:
            # Edge case: a zero-length note. Return an empty array.
            return np.zeros(0, dtype=np.float64)

        # Discrete-time vector over the note's duration. Using arange
        # rather than linspace prevents off-by-one phase drift between
        # consecutive notes whose durations are not integer seconds.
        t = np.arange(n_samples, dtype=np.float64) / self.sample_rate
        nyquist = self.sample_rate / 2.0

        if self.timbre == "sine":
            # The base case: a single sinusoid.
            wave = np.sin(2.0 * np.pi * frequency * t)
            return wave

        # Additive synthesis for the remaining timbres.
        wave = np.zeros(n_samples, dtype=np.float64)

        for k in range(1, self.num_harmonics + 1):
            harmonic_freq = k * frequency

            # Bandlimit: ignore any harmonic at or above Nyquist.
            if harmonic_freq >= nyquist:
                break

            if self.timbre == "sawtooth":
                # Includes all harmonics; weight 1/k, alternating sign.
                weight = ((-1) ** (k + 1)) / k
                wave += weight * np.sin(2.0 * np.pi * harmonic_freq * t)

            elif self.timbre == "square":
                # Only odd harmonics, equal-sign, weight 1/k.
                if k % 2 == 1:
                    wave += (1.0 / k) * np.sin(2.0 * np.pi * harmonic_freq * t)

            elif self.timbre == "triangle":
                # Only odd harmonics, weight 1/k^2, alternating sign.
                if k % 2 == 1:
                    sign = (-1) ** ((k - 1) // 2)
                    wave += (sign / (k * k)) * np.sin(
                        2.0 * np.pi * harmonic_freq * t
                    )

        # Peak-normalize this oscillator to [-1, 1] so that all timbres
        # have comparable loudness before the envelope is applied.
        peak = np.max(np.abs(wave))
        if peak > 0:
            wave = wave / peak

        return wave

    # ----- Envelope -------------------------------------------------------

    def envelope(self, n_samples):
        """
        Build a linear attack/decay (fade-in / fade-out) envelope.

        The envelope is a 1-D array of the same length as the note,
        ramping linearly from 0 -> 1 over `attack` seconds, holding
        at 1, then ramping linearly from 1 -> 0 over `decay` seconds.

        If the note is shorter than (attack + decay), the attack and
        decay are scaled down proportionally to fit, so the envelope
        always starts and ends at zero. This eliminates the click
        artifacts that come from suddenly switching a waveform on/off.

        Parameters
        ----------
        n_samples : int
            Length of the note in samples.

        Returns
        -------
        numpy.ndarray
            1-D float64 array of length n_samples in [0, 1].
        """
        if n_samples <= 0:
            return np.zeros(0, dtype=np.float64)

        attack_samples = self.sec2samples(self.attack)
        decay_samples = self.sec2samples(self.decay)

        # Shrink the ramps proportionally if the note is too short.
        if attack_samples + decay_samples > n_samples:
            total = attack_samples + decay_samples
            attack_samples = int(round(n_samples * attack_samples / total))
            decay_samples = n_samples - attack_samples

        sustain_samples = n_samples - attack_samples - decay_samples

        # Build each segment, then concatenate.
        # endpoint=False on the attack avoids a duplicate `1.0` sample
        # at the boundary with the sustain block.
        attack_curve = (
            np.linspace(0.0, 1.0, attack_samples, endpoint=False)
            if attack_samples > 0
            else np.zeros(0)
        )
        sustain_curve = np.ones(sustain_samples, dtype=np.float64)
        decay_curve = (
            np.linspace(1.0, 0.0, decay_samples, endpoint=True)
            if decay_samples > 0
            else np.zeros(0)
        )

        return np.concatenate([attack_curve, sustain_curve, decay_curve])

    # ----- One note -------------------------------------------------------

    def note(self, midi_note, beats, bpm):
        """
        Synthesize a single note (or rest) as a NumPy array.

        Parameters
        ----------
        midi_note : int
            MIDI note number in [0, 127], or any negative number to
            indicate a rest of the same duration.
        beats : float
            Duration in beats (1.0 = one quarter note at the given tempo).
        bpm : float
            Tempo in beats per minute.

        Returns
        -------
        numpy.ndarray
            The synthesized note (oscillator * envelope), or a block
            of silence of the appropriate length for a rest.
        """
        duration_sec = self.bpm2sec(bpm, beats)
        n_samples = self.sec2samples(duration_sec)

        # Rests: any negative MIDI number produces silence.
        if midi_note < 0:
            return np.zeros(n_samples, dtype=np.float64)

        freq = self.midi2freq(midi_note)
        osc = self.oscillator(freq, n_samples)
        env = self.envelope(n_samples)
        return osc * env

    # ----- The full melody ------------------------------------------------

    def melody(self, midi_notes, bpm, beats_per_note=None):
        """
        Synthesize a complete melody by concatenating notes.

        Parameters
        ----------
        midi_notes : list of int
            MIDI note numbers (negative = rest).
        bpm : float
            Tempo in beats per minute.
        beats_per_note : list of float or None
            Duration of each note in beats. If None, every note lasts
            one beat. Must be the same length as `midi_notes`.

        Returns
        -------
        numpy.ndarray
            The full melody as a 1-D float64 array, peak-normalized
            so that |sample| <= self.amplitude everywhere.
        """
        if not midi_notes:
            return np.zeros(0, dtype=np.float64)

        if beats_per_note is None:
            beats_per_note = [1.0] * len(midi_notes)

        if len(beats_per_note) != len(midi_notes):
            raise ValueError(
                "beats_per_note must be the same length as midi_notes."
            )

        # Synthesize each note independently and concatenate. This is
        # simpler and more reliable than writing into a preallocated
        # buffer, because rounding sec->samples may shift boundaries
        # by one sample between notes.
        segments = [
            self.note(m, b, bpm) for m, b in zip(midi_notes, beats_per_note)
        ]
        full = np.concatenate(segments)

        # Final peak normalization to the configured amplitude. This
        # protects against any clipping while keeping the output loud
        # enough to be audible on a typical sound card.
        peak = np.max(np.abs(full)) if full.size else 0.0
        if peak > 0:
            full = full * (self.amplitude / peak)

        return full

    # ----- Output ---------------------------------------------------------

    def play(self, audio):
        """
        Play an audio buffer through the default sound device.

        Blocks until playback is complete. Catches any sounddevice
        errors (no audio device, busy device, etc.) and reports them
        without crashing the script.

        `sounddevice` is imported here rather than at module top level
        so that environments without PortAudio (e.g. cloud Linux web
        hosts) can still import this module and run the web app.
        """
        if audio.size == 0:
            print("Nothing to play: the melody is empty.")
            return

        try:
            import sounddevice as sd
        except (OSError, ImportError) as exc:
            print(
                f"Audio playback unavailable on this system: {exc}. "
                "Save the melody to a WAV file instead."
            )
            return

        print("Playback starting...")
        try:
            sd.play(audio, self.sample_rate)
            sd.wait()
            print("Playback finished successfully.")
        except Exception as exc:
            print(f"Playback failed: {exc}")

    def save_wav(self, audio, path):
        """
        Write an audio buffer to a 16-bit PCM .wav file.

        Parameters
        ----------
        audio : numpy.ndarray
            Float audio in roughly [-1, 1].
        path : str
            Destination file path. A `.wav` extension is appended if
            the user forgot one.
        """
        if audio.size == 0:
            print("Nothing to save: the melody is empty.")
            return

        if not path.lower().endswith(".wav"):
            path += ".wav"

        print(f"Writing WAV file to {path} ...")
        try:
            sf.write(path, audio, self.sample_rate, subtype="PCM_16")
            print(f"WAV file written successfully: {path}")
        except Exception as exc:
            print(f"Failed to write WAV file: {exc}")


# ---------------------------------------------------------------------------
# Input helpers
# ---------------------------------------------------------------------------
#
# These functions are kept separate from the Synthesizer class because
# they belong to the user-interface layer, not the audio engine. Each one
# loops until it gets a valid response from the user.


def prompt_notes():
    """
    Prompt the user for a comma-separated list of MIDI notes.

    Returns
    -------
    list of int
        Parsed MIDI note numbers. Negative numbers are kept as-is so
        the synthesizer can interpret them as rests.

    Notes
    -----
    Re-prompts on any of the following errors:
        - empty input
        - non-integer tokens (e.g. "60.5", "abc")
        - any value greater than 127 (MIDI ceiling)
        - any value less than -127 (we allow negatives for rests, but a
          ridiculous magnitude is almost certainly a typo)
    """
    while True:
        raw = input(
            "Input the melody as a comma-separated list of MIDI note "
            "values (0-127, or any negative number for a rest): "
        ).strip()
        if not raw:
            print("  -> Please type at least one number.")
            continue
        try:
            notes = [int(x.strip()) for x in raw.split(",") if x.strip()]
        except ValueError:
            print("  -> All values must be whole numbers separated by commas.")
            continue
        if not notes:
            print("  -> No valid values were found. Try again.")
            continue
        # Validate range. Anything in [0, 127] is a legal MIDI note,
        # and anything negative is treated as a rest.
        bad = [n for n in notes if n > Synthesizer.MIDI_MAX or n < -Synthesizer.MIDI_MAX]
        if bad:
            print(
                f"  -> These values are outside the legal range: {bad}. "
                "MIDI notes must be between 0 and 127 "
                "(or negative for rests)."
            )
            continue
        return notes


def prompt_bpm():
    """
    Prompt the user for a tempo in BPM.

    Returns
    -------
    float
        A reasonable tempo, guaranteed to lie in (0, 1000].
    """
    while True:
        raw = input("Input the tempo in BPM (e.g. 120): ").strip()
        try:
            bpm = float(raw)
        except ValueError:
            print("  -> The tempo must be a single number (e.g. 120).")
            continue
        if bpm <= 0:
            print("  -> The tempo must be greater than 0.")
            continue
        if bpm > 1000:
            # Beyond ~1000 BPM each note is a few ms long; the attack
            # and decay times start to dominate and the output becomes
            # an unrecognizable buzz. Cap the input rather than silently
            # producing garbage.
            print(
                "  -> That tempo is unreasonably fast (>1000 BPM). "
                "Try something slower."
            )
            continue
        return bpm


def prompt_durations(num_notes):
    """
    Optionally prompt the user for per-note durations in beats.

    Parameters
    ----------
    num_notes : int
        Number of notes already entered. The durations list must match
        this length.

    Returns
    -------
    list of float or None
        Each entry is the duration of the corresponding note in beats
        (1.0 = one beat of the tempo, 0.5 = an eighth note at the same
        tempo, 2.0 = a half note, etc.). Returns None to indicate
        "use one beat per note", which is the default behavior.
    """
    while True:
        raw = input(
            f"Optional: input {num_notes} note durations in BEATS "
            "(e.g. '1,1,0.5,0.5,2' where 1=one beat at the given "
            "tempo), or press Enter to give every note one beat: "
        ).strip()
        if not raw:
            return None
        try:
            beats = [float(x.strip()) for x in raw.split(",") if x.strip()]
        except ValueError:
            print("  -> Durations must be numbers separated by commas.")
            continue
        if len(beats) != num_notes:
            print(
                f"  -> You entered {len(beats)} durations but the melody "
                f"has {num_notes} notes. They must match."
            )
            continue
        if any(b <= 0 for b in beats):
            print("  -> Each duration must be greater than zero beats.")
            continue
        return beats


def prompt_timbre():
    """
    Prompt the user for a timbre preset.

    Returns
    -------
    str
        One of "sine", "triangle", "sawtooth", "square".
    """
    presets = {
        "1": ("sine", "pure tone, no harmonics"),
        "2": ("triangle", "soft, flute-like"),
        "3": ("sawtooth", "bright, brassy"),
        "4": ("square", "hollow, clarinet/8-bit"),
    }
    print("Available timbres:")
    for k, (name, desc) in presets.items():
        print(f"  {k}: {name:8s} ({desc})")

    while True:
        raw = input("Choose a timbre by number [default 2 = triangle]: ").strip()
        if not raw:
            return "triangle"
        if raw in presets:
            return presets[raw][0]
        # Allow the user to type the name directly as a fallback.
        for name, _ in presets.values():
            if raw.lower() == name:
                return name
        print("  -> Please enter 1, 2, 3, or 4.")


def prompt_yes_no(question):
    """
    Prompt the user for a yes/no answer.

    Accepts y, yes, n, no in any combination of upper/lowercase. Loops
    until the response is unambiguous.

    Returns
    -------
    bool
        True for yes, False for no.
    """
    while True:
        raw = input(f"{question} (yes/no): ").strip().lower()
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("  -> Please answer 'yes' or 'no'.")


def prompt_wav_path():
    """
    Prompt the user for the path of the output WAV file.

    A blank entry defaults to 'melody.wav' in the current working
    directory. The .wav extension is added by save_wav() if missing.
    """
    raw = input(
        "Enter the WAV output filename [default: melody.wav]: "
    ).strip()
    return raw if raw else "melody.wav"


# ---------------------------------------------------------------------------
# Main script
# ---------------------------------------------------------------------------

def main():
    """
    Run the interactive synthesizer session.

    Order of operations:
        1. Greet the user.
        2. Collect melody, tempo, durations, and timbre.
        3. Build a Synthesizer with the chosen timbre.
        4. Synthesize the melody into a NumPy array.
        5. Offer playback.
        6. Offer to save a WAV file.
    """
    print("=" * 64)
    print("  Monophonic Additive Synthesizer")
    print("=" * 64)
    print(
        "This program will synthesize a melody you specify, optionally\n"
        "play it through your speakers, and optionally save it to a\n"
        "WAV file. MIDI note numbers run from 0 (very low) to 127 (very\n"
        "high); 69 is concert A (440 Hz), 60 is middle C.\n"
    )

    # 1. Melody
    notes = prompt_notes()

    # 2. Tempo
    bpm = prompt_bpm()

    # 3. Durations (bonus #2)
    durations = prompt_durations(len(notes))

    # 4. Timbre (bonus #3)
    timbre = prompt_timbre()

    # 5. Build the synthesizer with the chosen timbre. All other
    # parameters use sensible defaults.
    synth = Synthesizer(timbre=timbre)

    # 6. Synthesize.
    print("\nSynthesizing the melody...")
    try:
        audio = synth.melody(notes, bpm, durations)
    except Exception as exc:
        print(f"Synthesis failed: {exc}")
        sys.exit(1)

    duration_sec = audio.size / synth.sample_rate
    print(
        f"Done. Synthesized {len(notes)} notes, "
        f"total duration {duration_sec:.2f} seconds, "
        f"{audio.size} samples at {synth.sample_rate} Hz."
    )

    # 7. Playback.
    if prompt_yes_no("\nPlay the melody now?"):
        synth.play(audio)

    # 8. WAV output.
    if prompt_yes_no("\nSave the melody to a WAV file?"):
        path = prompt_wav_path()
        # If the user provided just a filename, drop it in the current
        # working directory; if they gave a full path, respect it.
        if not os.path.dirname(path):
            path = os.path.join(os.getcwd(), path)
        synth.save_wav(audio, path)

    print("\nAll done. Goodbye!")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        # A clean exit on Ctrl-C is much friendlier than a stack trace.
        print("\nInterrupted by user. Exiting.")
        sys.exit(0)
