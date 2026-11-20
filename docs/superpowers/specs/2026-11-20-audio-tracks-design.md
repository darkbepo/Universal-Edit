# Audio editor: several tracks and mixing — design

Date: 2026-11-20 · Both apps (desktop + website) · Roadmap 10.13, last "Not yet" item.

## Goal

Put several sounds on top of each other (voice over music, two takes, a beat under a melody),
set each one's volume and left / right position, and save the mix as one file. Everything the
editor does today keeps working on the track being edited.

## What the user sees

- A **Tracks** strip above the waveform, one row per track:
  name (click = edit this track), **M** (mute), **S** (solo), volume (−24 … +12 dB),
  pan (left … right), **×** remove (asks first if the track holds sound).
- **+ Track** adds a track: *Empty* (then Record or Paste into it) or *From a file…*.
  Dropping files while a sound is open still replaces it (joined), as now.
- The waveform shows the selected track. Cut, effects, typed times, recording, undo … act on it.
- **Play** plays the mix of the tracks you can hear (solo wins, muted ones are silent) when
  there are two or more; with one track it plays it as before.
- **Save / Save as** write the mix when there are two or more tracks ("Saves the mix of 3
  tracks" in the status line). One track saves exactly as today.

## Model (Qt-free / DOM-free, line-by-line ports)

`audioedit/tracks.py` ↔ `web/src/audioedit/tracks.ts`:

- `Track(doc, name, gain_db=0, pan=0, mute=False, solo=False)`; every track keeps its own
  `AudioDoc` (own undo history). All tracks share the first track's sample rate (files added
  later are decoded at it); each keeps its own channel count.
- `audible(tracks)` — solo wins over the rest; muted tracks never play.
- `pan_gains(pan)` — balance law: left = min(1, 1 − pan), right = min(1, 1 + pan)
  (centre = both at full, so a centred track sounds as loud as alone).
- `mix(tracks, channels, start, end)` — output channels = the most any track has; length = the
  longest audible track; each track fitted to the output channels (`ops.fit_channels`), times
  dB gain, times pan (stereo output only), summed track by track. No limiter: peaks over 0 dB
  are reported ("The mix peaks at +2.1 dB — turn a track down or Normalize after saving").
- Limits: the samples of all tracks together stay within `MAX_SAMPLES`.
- Shared fixture `tests/fixtures/audio_mix_cases.json` (MINSTD signals), written by
  `scripts/make_mix_cases.py`; both suites check it (web within 1e-6).

## Not in this step

Moving a track in time (paste / Record at the cursor places sound), per-track effects
automation, saving the tracks separately / a project file, more than stereo output.
Track settings (volume, pan, mute, solo, add, remove) aren't undo steps — like a mixing desk.
