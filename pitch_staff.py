#!/usr/bin/env python3
"""Pitch staff: the note you're singing, written on a staff.

Tenor clef as usually written (treble with a small 8 below: notes sound an
octave lower than written). The note sung is an eighth note on its line or
space, spelled for the chosen key, with an accidental when the key signature
doesn't already cover it. Green when in tune; red with an arrow when flat
(down) or sharp (up).

Audio and pitch detection are the pitch monitor's (pitch_monitor.py). On top:

Vibrato. The pitch swings 5-7 times a second, by up to a semitone either
way, around the pitch that's heard. So the note and its tuning come from
the average pitch over the last 350 ms, about two swings: they cancel
out and leave the centre, which holds still through a steady vibrato.

Note changes. A leap of more than LEAP semitones that holds for a few frames
starts a new note, with a fresh average; a short blip (a frame or two an
octave out) is ignored. A pause starts afresh too. To stop the written note
flickering between two when the pitch sits halfway, it only moves once the
centre is HYSTERESIS past halfway.

Keys: Left / Right go round the circle of fifths (or pick the key at the top
left); Q / Esc quit. The key is remembered.
"""

import os
import statistics
import sys

from PyQt6.QtCore import QPointF, QRectF, QSettings, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QFontMetricsF, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QApplication, QComboBox, QWidget

from pitch_monitor import Listener

IN_TUNE = 15        # cents either way that count as in tune
WINDOW = 0.35       # seconds of pitch averaged: two swings of vibrato
GAP = 0.12          # seconds of silence that end a note
LEAP = 1.5          # semitones: further than vibrato goes
LEAP_FRAMES = 4     # 40 ms a leap has to hold to be a new note
HYSTERESIS = 0.1    # semitones past halfway before the written note moves

# SMuFL code points in Bravura
CLEF = ""                                  # gClef8vb
NOTE_UP, NOTE_DOWN = "", ""          # eighth notes
HEAD = ""                                  # black notehead
ACCIDENTAL = {-2: "", -1: "", 0: "", 1: "", 2: ""}
TEXT_ACC = {-2: "𝄫", -1: "♭", 0: "", 1: "♯", 2: "𝄪"}

LETTERS = "CDEFGAB"
FIFTHS = "FCGDAEB"                 # the line of fifths, F at -1
MAJOR = ["C♭", "G♭", "D♭", "A♭", "E♭", "B♭", "F", "C", "G", "D", "A", "E", "B", "F♯", "C♯"]
MINOR = ["A♭", "E♭", "B♭", "F", "C", "G", "D", "A", "E", "B", "F♯", "C♯", "G♯", "D♯", "A♯"]
# where the key signature goes on a treble staff, as written notes (letter, octave)
SHARP_SIG = [("F", 5), ("C", 5), ("G", 5), ("D", 5), ("A", 4), ("E", 5), ("B", 4)]
FLAT_SIG = [("B", 4), ("E", 5), ("A", 4), ("D", 5), ("G", 4), ("C", 5), ("F", 4)]

E4 = 4 * 7 + 2                     # staff step of the bottom line
B4 = 4 * 7 + 6                     # the middle line


def key_name(k):
    count = f"  ({abs(k)}{'♯' if k > 0 else '♭'})" if k else ""
    return f"{MAJOR[k + 7]} major / {MINOR[k + 7]} minor{count}"


def spell(midi, k):
    """(letter, alter, octave) of a MIDI note in the key with k sharps (k < 0: flats).

    Of the two ways to write a pitch, take the one nearer the key on the line
    of fifths, so the key's own notes come out as in its scale, and the others
    as the usual chromatic spelling (in C: C♯ E♭ F♯ G♯ B♭). Never a double
    sharp or flat (G♮ rather than F𝄪 in F♯ major); a tie goes to the fewer
    accidentals (B♮ rather than C♭ in E♭), then sharps in sharp keys.
    """
    q = (7 * midi) % 12            # one position of this pitch class on the line of fifths
    centre = k + 2                 # middle of the key's seven notes
    options = [q - 12, q, q + 12]
    q = min(options, key=lambda o: (abs((o + 1) // 7) > 1, abs(o - centre), abs((o + 1) // 7),
                                    o < centre if k >= 0 else o > centre))
    letter = FIFTHS[(q + 1) % 7]
    alter = (q + 1) // 7
    octave = (midi - alter) // 12 - 1
    return letter, alter, octave


def key_alter(letter, k):
    if k > 0 and letter in "FCGDAEB"[:k]:
        return 1
    if k < 0 and letter in "BEADGCF"[:-k]:
        return -1
    return 0


class NoteTracker:
    """Turns the stream of pitches into a steady note and how far off it is."""

    def __init__(self):
        self.seg = []         # (t, midi) of the current note, last WINDOW seconds
        self.pending = []     # frames that might be a leap to a new note
        self.last_t = -1.0
        self.voiced_t = None
        self.centre = None    # average pitch, MIDI float
        self.note = None      # the note written, MIDI int

    def feed(self, t, m):
        self.last_t = t
        if m is None:
            if self.voiced_t is not None and t - self.voiced_t > GAP:
                self.seg, self.pending = [], []
                self.centre = self.note = self.voiced_t = None
            return
        self.voiced_t = t
        if self.centre is not None and abs(m - self.centre) > LEAP:
            self.pending.append((t, m))
            leap = [p for _, p in self.pending]
            if len(self.pending) < LEAP_FRAMES or max(leap) - min(leap) > LEAP:
                self.pending = self.pending[-LEAP_FRAMES:]
                return
            self.seg, self.pending, self.note = self.pending, [], None
        else:
            self.pending = []
            self.seg.append((t, m))
        self.seg = [(s, p) for s, p in self.seg if t - s <= WINDOW]
        pitches = [p for _, p in self.seg]
        med = statistics.median(pitches)
        self.centre = statistics.fmean(p for p in pitches if abs(p - med) <= LEAP)
        if self.note is None or abs(self.centre - self.note) > 0.5 + HYSTERESIS:
            self.note = round(self.centre)

    def cents(self):
        return (self.centre - self.note) * 100


class Staff(QWidget):
    def __init__(self, listener, font_family):
        super().__init__()
        self.listener = listener
        self.tracker = NoteTracker()
        self.family = font_family
        self.settings = QSettings("pitch-staff", "pitch-staff")
        self.key = int(self.settings.value("key", 0))
        self.setWindowTitle("Pitch Staff")
        self.resize(720, 460)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.keys = QComboBox(self)
        self.keys.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for k in range(-7, 8):
            self.keys.addItem(key_name(k), k)
        self.keys.setCurrentIndex(self.key + 7)
        self.keys.currentIndexChanged.connect(lambda i: self.set_key(i - 7))
        self.keys.move(12, 10)
        self.keys.adjustSize()

        timer = QTimer(self)
        timer.timeout.connect(self.tick)
        timer.start(16)

    def set_key(self, k):
        self.key = max(-7, min(7, k))
        self.settings.setValue("key", self.key)
        if self.keys.currentIndex() != self.key + 7:
            self.keys.setCurrentIndex(self.key + 7)
        self.update()

    def tick(self):
        new = []
        for t, m in reversed(self.listener.track):
            if t <= self.tracker.last_t:
                break
            new.append((t, m))
        for t, m in reversed(new):
            self.tracker.feed(t, m)
        self.update()

    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key.Key_Q, Qt.Key.Key_Escape):
            self.close()
        elif k == Qt.Key.Key_Right:
            self.set_key(self.key + 1)
        elif k == Qt.Key.Key_Left:
            self.set_key(self.key - 1)

    # -- drawing -------------------------------------------------------

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor("#16161e"))
        ink = QColor("#c0caf5")

        sp = min(h / 16, w / 20)           # staff space
        mid = h / 2 + sp                   # the middle line, a little low: room for the key menu
        y_of = lambda step: mid - (step - B4) * sp / 2
        left, right = sp, w - sp

        music = QFont(self.family)
        music.setPixelSize(round(4 * sp))  # SMuFL: an em is four staff spaces
        fm = QFontMetricsF(music)
        p.setFont(music)

        # staff
        p.setPen(QPen(ink, 0.13 * sp))
        for i in range(5):
            y = y_of(E4 + 2 * i)
            p.drawLine(QPointF(left, y), QPointF(right, y))

        # clef, on the G line
        p.setPen(ink)
        x = left + 0.6 * sp
        p.drawText(QPointF(x, y_of(E4 + 2)), CLEF)
        x += fm.horizontalAdvance(CLEF) + 0.8 * sp

        # key signature
        sig = SHARP_SIG if self.key > 0 else FLAT_SIG
        glyph = ACCIDENTAL[1 if self.key > 0 else -1]
        for letter, octave in sig[:abs(self.key)]:
            p.drawText(QPointF(x, y_of(octave * 7 + LETTERS.index(letter))), glyph)
            x += fm.horizontalAdvance(glyph) + 0.15 * sp

        note_x = max(x + 3 * sp, (x + right) / 2 - sp)
        tr = self.tracker
        if tr.note is None:
            return

        # the note: written an octave above how it sounds
        letter, alter, octave = spell(tr.note, self.key)
        step = (octave + 1) * 7 + LETTERS.index(letter)
        y = y_of(step)
        cents = tr.cents()
        in_tune = abs(cents) <= IN_TUNE
        colour = QColor("#9ece6a") if in_tune else QColor("#f7768e")
        head_w = fm.horizontalAdvance(HEAD)

        # ledger lines, in the staff's colour
        p.setPen(QPen(ink, 0.16 * sp))
        for s in list(range(E4 - 2, step - 1, -2)) + list(range(E4 + 10, step + 1, 2)):
            ly = y_of(s)
            p.drawLine(QPointF(note_x - 0.4 * sp, ly), QPointF(note_x + head_w + 0.4 * sp, ly))

        p.setPen(colour)
        if alter != key_alter(letter, self.key):
            acc = ACCIDENTAL[alter]
            p.drawText(QPointF(note_x - fm.horizontalAdvance(acc) - 0.3 * sp, y), acc)
        glyph = NOTE_DOWN if step >= B4 else NOTE_UP
        p.drawText(QPointF(note_x, y), glyph)

        # tuning arrow: up if sharp, down if flat, with the cents
        ax = note_x + fm.horizontalAdvance(glyph) + 0.8 * sp
        if not in_tune:
            d = -1 if cents > 0 else 1        # screen y goes down
            length, head = 1.6 * sp, 0.45 * sp
            tip = QPointF(ax, y + d * length / 2)
            p.setPen(QPen(colour, 0.18 * sp, cap=Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(ax, y - d * length / 2), tip)
            arrow = QPainterPath(tip)
            arrow.lineTo(ax - head * 0.7, tip.y() - d * head)
            arrow.lineTo(ax + head * 0.7, tip.y() - d * head)
            arrow.closeSubpath()
            p.fillPath(arrow, colour)

        # readout: the note as it sounds, and cents off
        small = QFont()
        small.setPixelSize(max(12, round(sp * 0.55)))
        p.setFont(small)
        p.setPen(colour)
        sign = "+" if cents >= 0 else "−"
        text = f"{letter}{TEXT_ACC[alter]}{octave}   {sign}{abs(cents):.0f}¢"
        p.drawText(QRectF(0, 10, w - 14, small.pixelSize() * 1.6),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, text)
        p.end()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Pitch Staff")
    app.setDesktopFileName("pitch-staff")
    font = QFontDatabase.addApplicationFont(os.environ["PITCH_STAFF_FONT"])
    families = QFontDatabase.applicationFontFamilies(font)
    if not families:
        sys.exit(f"can't load the music font {os.environ['PITCH_STAFF_FONT']}")
    listener = Listener("Pitch staff")
    listener.start()
    win = Staff(listener, families[0])
    win.show()
    code = app.exec()
    listener.stop()
    sys.exit(code)


if __name__ == "__main__":
    main()
