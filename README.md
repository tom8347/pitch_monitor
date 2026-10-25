# Pitch monitor

Two small desktop apps for singing practice on Linux. Both listen to the
default microphone (through PipeWire) and show the pitch you sing:

- **Pitch Monitor** (`pitch-monitor`): your pitch drawn as a line scrolling past
  a piano keyboard, like the Android app
  [Vocal Pitch Monitor](https://play.google.com/store/apps/details?id=com.tadaoyamaoka.vocalpitchmonitor).
- **Pitch Staff** (`pitch-staff`): the note you're singing as an eighth note on a
  staff, written for the key you choose, coloured for whether it's in tune.

## Run

```sh
nix run github:tom8347/pitch_monitor                # Pitch Monitor
nix run github:tom8347/pitch_monitor#pitch-staff    # Pitch Staff
```

Both open as native Wayland windows (X11 works too) and record from whichever
input is the PipeWire default.

## Pitch Monitor

A line on each semitone: solid on the white keys, broken on the black keys,
bold on every C. The note, how many cents off it is, and the frequency are in
the top left. It follows your voice up and down, two octaves and a third at a
time.

| Key / mouse   | Does                                             |
| ------------- | ------------------------------------------------ |
| Wheel         | Move up and down the keyboard                    |
| Ctrl+wheel    | Zoom: more or fewer semitones on screen          |
| Shift+wheel   | Longer or shorter time span (8 s to start with)  |
| Space         | Pause / resume                                   |
| F             | Follow your voice on or off                      |
| Up / Down     | Move a semitone; Home re-centres on the last note |
| Q / Esc       | Quit                                             |

## Pitch Staff

- **Staff:** a treble clef with a small 8 below it, as tenor parts are usually
  written, so notes sound an octave lower than written.
- **Key:** pick it from the menu at the top left, or step round the circle of
  fifths with Left / Right. It's remembered between runs (in
  `~/.config/pitch-staff/`).
- **Spelling:** notes are spelled for the key, with an accidental only where the
  key signature doesn't already cover it. Notes outside the key get the usual
  chromatic spelling (in C: C♯ E♭ F♯ G♯ B♭), never a double sharp or flat.
- **Tuning:** green within ±15 cents. Otherwise red, with an arrow up when sharp
  or down when flat. The note as it sounds and the cents off are in the top
  right.
- **Vibrato:** the note and its tuning come from the average pitch over the
  last 350 ms, about two vibrato swings, so a steady vibrato reads as the
  pitch you hear rather than swinging in and out of tune. A jump of more than
  1.5 semitones that holds for 40 ms starts a new note at once; a blip of a
  frame or two (an octave slip, say) is ignored.

Q / Esc quits.

## How it works

`pw-record` streams mono 24 kHz audio. Every 10 ms the last 64 ms go through
[YIN](http://audition.ens.fr/adc/pdf/2002_JASA_YIN.pdf) for the fundamental
(55–1400 Hz, which covers any voice); quiet or unpitched frames are gaps.
PipeWire hands the audio over in bursts, so each pitch is timed by its place in
the audio rather than by when it arrived, and the display scrolls on a clock
locked to the audio.

Pitch Staff reuses Pitch Monitor's audio and pitch detection, and adds the
note tracking (averaging, new notes, hysteresis) and the notation, drawn in
Steinberg's [Bravura](https://github.com/steinbergmedia/bravura) font
(SIL Open Font License; the package downloads a pinned copy).

The settings likely to want tuning are constants at the top of each file:
`SILENCE` and `THRESHOLD` in `pitch_monitor.py` (what counts as singing), and
`IN_TUNE` and `WINDOW` in `pitch_staff.py`.

## Develop

```sh
direnv allow      # or: nix develop
pitch-monitor     # runs the working tree
pitch-staff
```

The dev shell has Python with numpy, PyQt6 and the editor tools (pylsp, mypy,
rope, pynvim, IPython), plus `pw-record` and the Qt Wayland plugin.

## Install (Home Manager)

As a flake input:

```nix
inputs.pitch-monitor = {
  url = "github:tom8347/pitch_monitor";
  inputs.nixpkgs.follows = "nixpkgs";
};
```

then add `inputs.pitch-monitor.packages.${pkgs.stdenv.hostPlatform.system}.default`
to `home.packages`. That installs both commands and their desktop entries.
After pushing changes here, `nix flake update pitch-monitor` in the consuming
flake picks them up.
