#!/usr/bin/env python3
"""Vocal pitch monitor: sing into the default PipeWire source and see your
pitch scroll past on a piano roll, like the Android app Vocal Pitch Monitor.

Audio comes from pw-record (mono float, 24 kHz). Every 10 ms the last 64 ms
are run through YIN to get the fundamental; quiet or unpitched frames leave a
gap. The newest pitch is on the right, the last few seconds scroll left.

PipeWire hands the audio over in bursts (every ~43 ms here), so each pitch is
timed by its place in the audio, not by when it arrived, and the display
scrolls on a clock locked to the audio. Timed on arrival, a burst's pitches
all landed at one x, which drew the trace as a staircase.

Keys and mouse:
  wheel         move up and down the keyboard
  Ctrl+wheel    zoom (more or fewer semitones on screen)
  Shift+wheel   longer or shorter time span
  Space         pause / resume
  F             follow your voice (re-centres when you go off screen)
  Up / Down     move a semitone; Home re-centres on the last note
  Q, Esc        quit
"""

import collections
import math
import subprocess
import sys
import threading
import time

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QApplication, QWidget

RATE = 24000
HOP = RATE // 100           # 10 ms between pitch estimates
WINDOW = 1536               # 64 ms of audio per estimate
FMIN, FMAX = 55.0, 1400.0   # A1 to about F6: any voice
THRESHOLD = 0.15            # YIN aperiodicity; higher lets more through
SILENCE = 0.004             # RMS below this is not singing

NAMES = ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"]
BLACK = {1, 3, 6, 8, 10}


def note_name(midi):
    n = int(round(midi))
    return f"{NAMES[n % 12]}{n // 12 - 1}"


def yin(x):
    """Fundamental frequency of x in Hz, or None if it isn't pitched."""
    x = x - x.mean()
    w = len(x) // 2
    tau_min, tau_max = int(RATE / FMAX), min(int(RATE / FMIN), w)
    # d(tau) = sum over j < w of (x[j] - x[j+tau])^2, via one FFT correlation
    n = 1 << (len(x) + w - 1).bit_length()
    r = np.fft.irfft(np.conj(np.fft.rfft(x[:w], n)) * np.fft.rfft(x, n), n)[:tau_max + 1]
    sq = np.concatenate(([0.0], np.cumsum(x * x)))
    energy = sq[w:w + tau_max + 1] - sq[:tau_max + 1]
    d = sq[w] + energy - 2 * r
    d[0] = 0
    # cumulative mean normalised difference
    cmnd = np.ones_like(d)
    cs = np.cumsum(d[1:])
    cmnd[1:] = d[1:] * np.arange(1, tau_max + 1) / np.maximum(cs, 1e-12)
    below = np.nonzero(cmnd[tau_min:tau_max] < THRESHOLD)[0]
    if not len(below):
        return None
    tau = tau_min + below[0]
    while tau + 1 < tau_max and cmnd[tau + 1] < cmnd[tau]:
        tau += 1
    # parabolic interpolation around the dip
    a, b, c = cmnd[tau - 1], cmnd[tau], cmnd[tau + 1]
    den = a - 2 * b + c
    shift = 0.5 * (a - c) / den if den else 0.0
    return RATE / (tau + shift)


class Listener(threading.Thread):
    """Reads the microphone and appends (time, midi or None) to self.track.

    Times are audio time: seconds of audio read so far.
    """

    def __init__(self, name="Pitch monitor"):
        super().__init__(daemon=True)
        self.name = name
        self.track = collections.deque(maxlen=100 * 120)  # two minutes
        self.level = 0.0
        self.error = None
        self.proc = None
        self.offset = None  # wall clock minus audio time, at its smallest

    def clock(self):
        """Audio time now, extrapolated smoothly between bursts."""
        return time.monotonic() - self.offset if self.offset is not None else 0.0

    def run(self):
        try:
            self.proc = subprocess.Popen(
                ["pw-record", "--raw", "--rate", str(RATE), "--channels", "1",
                 "--format", "f32", "--latency", "10ms",
                 "--properties", f'{{ "media.name": "{self.name}" }}', "-"],
                stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
        except OSError as e:
            self.error = f"can't run pw-record: {e}"
            return
        buf = np.zeros(WINDOW, dtype=np.float32)
        size = HOP * 4
        samples = 0
        while True:
            chunk = self.proc.stdout.read(size)
            if len(chunk) < size:
                self.error = "pw-record stopped: no microphone?"
                return
            buf = np.concatenate((buf[HOP:], np.frombuffer(chunk, dtype=np.float32)))
            rms = float(np.sqrt(np.mean(buf[-HOP * 4:] ** 2)))
            self.level = rms
            f = yin(buf.astype(np.float64)) if rms > SILENCE else None
            midi = 69 + 12 * math.log2(f / 440) if f else None
            samples += HOP
            t = samples / RATE
            # the last hop of each burst arrives soonest after it was heard:
            # follow those, and drift up slowly in case the clocks run apart
            off = time.monotonic() - t
            if self.offset is None or off < self.offset:
                self.offset = off
            else:
                self.offset += (off - self.offset) * 0.001
            self.track.append((t, midi))

    def stop(self):
        if self.proc:
            self.proc.terminate()


class Monitor(QWidget):
    KEYS_W = 56

    def __init__(self, listener):
        super().__init__()
        self.listener = listener
        self.setWindowTitle("Pitch Monitor")
        self.resize(900, 560)
        self.centre = 57.0      # A3, middle of a typical range
        self.span = 28.0        # semitones on screen: two octaves and a third
        self.seconds = 12.0
        self.paused_at = None   # time frozen at when paused
        self.follow = True
        self.last = None        # (midi, time) of the latest pitched frame
        timer = QTimer(self)
        timer.timeout.connect(self.tick)
        timer.start(16)

    # -- state ---------------------------------------------------------

    def now(self):
        return self.paused_at if self.paused_at is not None else self.listener.clock()

    def tick(self):
        if self.paused_at is None:
            for t, m in reversed(self.listener.track):
                if m is not None:
                    self.last = (m, t)
                    break
            if self.follow and self.last and self.listener.clock() - self.last[1] < 0.2:
                m = self.last[0]
                margin = self.span * 0.15
                if not (self.centre - self.span / 2 + margin < m < self.centre + self.span / 2 - margin):
                    self.centre += (m - self.centre) * 0.1
        self.update()

    def y_of(self, midi):
        top = self.centre + self.span / 2
        return (top - midi) / self.span * self.height()

    # -- input ---------------------------------------------------------

    def wheelEvent(self, e):
        steps = e.angleDelta().y() / 120
        mods = e.modifiers()
        if mods & Qt.KeyboardModifier.ControlModifier:
            self.span = min(88.0, max(6.0, self.span * 0.9 ** steps))
        elif mods & Qt.KeyboardModifier.ShiftModifier:
            self.seconds = min(60.0, max(2.0, self.seconds * 0.9 ** steps))
        else:
            self.centre = min(108.0, max(21.0, self.centre + steps * 2))
            self.follow = False

    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key.Key_Q, Qt.Key.Key_Escape):
            self.close()
        elif k == Qt.Key.Key_Space:
            self.paused_at = None if self.paused_at is not None else self.listener.clock()
        elif k == Qt.Key.Key_F:
            self.follow = not self.follow
        elif k == Qt.Key.Key_Up:
            self.centre += 1
            self.follow = False
        elif k == Qt.Key.Key_Down:
            self.centre -= 1
            self.follow = False
        elif k == Qt.Key.Key_Home and self.last:
            self.centre = self.last[0]

    # -- drawing -------------------------------------------------------

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor("#16161e"))
        lo = math.floor(self.centre - self.span / 2) - 1
        hi = math.ceil(self.centre + self.span / 2) + 1
        row = h / self.span
        live = (self.paused_at is None and self.last
                and self.listener.clock() - self.last[1] < 0.15)
        current = round(self.last[0]) if live else None

        # a line on each semitone: solid on white keys, broken on black keys,
        # bold on C
        label_font = QFont()
        label_font.setPixelSize(max(9, min(13, int(row * 0.7))))
        p.setFont(label_font)
        for n in range(lo, hi + 1):
            black = n % 12 in BLACK
            if n % 12 == 0:
                pen = QPen(QColor("#565f89"), 2)
                y = round(self.y_of(n))         # crisp 2px line
            else:
                pen = QPen(QColor("#3b4061"), 1)
                if black:
                    pen.setDashPattern([4, 4])
                y = round(self.y_of(n)) + 0.5   # crisp 1px line
            p.setPen(pen)
            p.drawLine(QPointF(self.KEYS_W, y), QPointF(w, y))

            # the keyboard: each key centred on its line
            key = QRectF(0, self.y_of(n + 0.5), self.KEYS_W, row)
            colour = QColor("#2a2b36") if black else QColor("#c9cbd6")
            if n == current:
                colour = QColor("#7aa2f7")
            p.fillRect(key.adjusted(0, 0.5, -1, -0.5), colour)
            if n % 12 == 0 or n == current or row >= 16:
                p.setPen(QColor("#16161e") if not black or n == current else QColor("#a9b1d6"))
                p.drawText(key.adjusted(4, 0, -4, 0),
                           Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                           note_name(n))

        # the pitch trace, broken at silences and at big leaps (octave slips)
        now = self.now()
        x_of = lambda t: w - (now - t) / self.seconds * (w - self.KEYS_W)
        pen = QPen(QColor("#9ece6a"), 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        path = QPainterPath()
        prev = None
        for t, m in list(self.listener.track):
            if m is None or now - t > self.seconds or t > now:
                prev = None
                continue
            pt = QPointF(x_of(t), self.y_of(m))
            if prev is not None and abs(prev - m) < 5:
                path.lineTo(pt)
            else:
                path.moveTo(pt)
                path.lineTo(pt + QPointF(0.5, 0))  # a lone frame still shows
            prev = m
        p.drawPath(path)

        # readout: note, cents off, frequency
        if self.last:
            m = self.last[0]
            cents = (m - round(m)) * 100
            hz = 440 * 2 ** ((m - 69) / 12)
            big = QFont()
            big.setPixelSize(42)
            big.setBold(True)
            p.setFont(big)
            p.setPen(QColor("#c0caf5") if live else QColor("#565f89"))
            box = QRectF(self.KEYS_W + 16, 10, 240, 50)
            p.drawText(box, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, note_name(m))
            small = QFont()
            small.setPixelSize(15)
            p.setFont(small)
            sign = "+" if cents >= 0 else "−"
            in_tune = abs(cents) < 10
            p.setPen(QColor("#9ece6a") if in_tune and live else QColor("#e0af68") if live else QColor("#565f89"))
            p.drawText(QRectF(self.KEYS_W + 16, 58, 240, 20), Qt.AlignmentFlag.AlignLeft,
                       f"{sign}{abs(cents):.0f} cents   {hz:.1f} Hz")

        # status line
        status = []
        if self.paused_at is not None:
            status.append("PAUSED (space)")
        if self.follow:
            status.append("follow")
        if self.listener.error:
            status.append(self.listener.error)
        if status:
            f = QFont()
            f.setPixelSize(13)
            p.setFont(f)
            p.setPen(QColor("#f7768e") if self.listener.error else QColor("#7982a9"))
            p.drawText(QRectF(self.KEYS_W, 10, w - self.KEYS_W - 14, 20),
                       Qt.AlignmentFlag.AlignRight, "  ·  ".join(status))

        # input level, a thin bar along the bottom
        lvl = min(1.0, math.sqrt(self.listener.level) * 4)
        p.fillRect(QRectF(self.KEYS_W, h - 3, (w - self.KEYS_W) * lvl, 3), QColor("#414868"))
        p.end()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Pitch Monitor")
    app.setDesktopFileName("pitch-monitor")
    listener = Listener()
    listener.start()
    win = Monitor(listener)
    win.show()
    code = app.exec()
    listener.stop()
    sys.exit(code)


if __name__ == "__main__":
    main()
