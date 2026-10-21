#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NO HAY BANDA
a terminal picture in three acts

    python3 nohaybanda.py             play
    python3 nohaybanda.py --silent    no drone, no voices
    python3 nohaybanda.py --fast      no typewriter
    python3 nohaybanda.py --forget    erase what it remembers about you

Any key hurries the text to the next choice. Ctrl-C leaves.
It remembers you between runs. That is the point.
"""
import argparse, atexit, datetime, getpass, json, math, os, random, re
import shutil, subprocess, sys, textwrap, threading, time, wave
try:
    import curses
except ImportError:
    sys.exit('This picture needs the curses module. On Windows:  pip install windows-curses')

HERE = os.path.dirname(os.path.abspath(__file__))
SAVE_DIR = os.path.join(HERE, 'save')
MEM_PATH = os.path.join(SAVE_DIR, 'memory.json')
GLYPHS = '#%&@$░▒▓█ΣΩ∞≈≠0123456789'
DEFAULT_MESSAGE = 'Laurent is dead.'


def esc(s):
    """text that came from a person, not from the script: keep braces out of the formatter"""
    return str(s).replace('{', '{{').replace('}', '}}')


# ----------------------------------------------------------------------------
# memory: what the picture knows about you
# ----------------------------------------------------------------------------
def load_memory():
    m = {'runs': 0, 'names': [], 'last_message': None, 'last_inputs': [],
         'endings': [], 'first_played': None, 'last_played': None, 'cowboy_seen': 0}
    try:
        with open(MEM_PATH) as f:
            m.update(json.load(f))
    except Exception:
        pass
    return m


def save_memory(m):
    os.makedirs(SAVE_DIR, exist_ok=True)
    tmp = MEM_PATH + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(m, f, indent=2)
    os.replace(tmp, MEM_PATH)


# ----------------------------------------------------------------------------
# audio: a drone, a horn, and voices that come out of your own machine
#
#   macOS    afplay for sound, say for voices
#   Windows  winsound (ships with Python) for sound, SAPI via PowerShell for voices
#   Linux    paplay / aplay / ffplay for sound, espeak-ng / espeak / spd-say for voices
#
# The drone is a 14 s unit built so that every oscillator completes a whole
# number of cycles per loop and the noise is crossfaded across the seam. It is
# tiled into a ~3 minute file with a short fade at each end, and the next
# player is started 1.5 s before the current one ends, so they crossfade
# instead of leaving a gap while the player process starts.
# ----------------------------------------------------------------------------
class Audio:
    RATE = 22050
    UNIT_SECS = 14.0
    TILES = 12
    XFADE = 1.5
    DRONE_LEVEL = 0.35
    HORN_LEVEL = 0.6

    def __init__(self, enabled):
        self.player = None
        self.speaker = None
        self.procs = []
        self._stop = threading.Event()
        self._thread = None
        self._drone_on = False
        self.loop_path = os.path.join(SAVE_DIR, 'drone_loop.wav')
        self.long_path = os.path.join(SAVE_DIR, 'drone_long.wav')
        self.horn_path = os.path.join(SAVE_DIR, 'horn.wav')
        self.long_secs = self.UNIT_SECS * self.TILES
        if enabled:
            self._detect()
        self.voices = self._mac_voices() if self.speaker == 'say' else []
        if self.player:
            os.makedirs(SAVE_DIR, exist_ok=True)
            if not (os.path.exists(self.loop_path) and os.path.exists(self.long_path)):
                self._build_drone()
            if not os.path.exists(self.horn_path):
                self._write_wav(self.horn_path, self._horn_samples())
        atexit.register(self.stop_all)

    # --- platform
    def _detect(self):
        w = shutil.which
        if sys.platform == 'darwin':
            self.player = 'afplay' if w('afplay') else None
            self.speaker = 'say' if w('say') else None
        elif sys.platform.startswith('win'):
            try:
                import winsound  # noqa: F401
                self.player = 'winsound'
            except ImportError:
                self.player = None
            self.speaker = 'powershell' if w('powershell') else None
        else:
            for c in ('paplay', 'aplay', 'ffplay'):
                if w(c):
                    self.player = c; break
            for c in ('espeak-ng', 'espeak', 'spd-say'):
                if w(c):
                    self.speaker = c; break

    @staticmethod
    def _mac_voices():
        try:
            out = subprocess.run(['say', '-v', '?'], capture_output=True, text=True, timeout=5).stdout
        except Exception:
            return []
        names = []
        for line in out.splitlines():
            m = re.match(r'^(.*?)\s+[a-z]{2,3}[_-][A-Za-z0-9]+\s', line)
            if m:
                names.append(m.group(1).strip())
        return names

    def pick_voice(self, prefs):
        for p in prefs:
            for v in self.voices:
                if v.lower() == p.lower():
                    return v
        return None

    # --- synthesis
    @classmethod
    def _to_int16(cls, samples):
        from array import array
        a = array('h', (int(max(-1.0, min(1.0, x)) * 32000) for x in samples))
        if sys.byteorder == 'big':
            a.byteswap()
        return a

    @classmethod
    def _write_wav(cls, path, samples):
        cls._write_wav_bytes(path, cls._to_int16(samples).tobytes())

    @classmethod
    def _write_wav_bytes(cls, path, data):
        with wave.open(path, 'w') as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(cls.RATE)
            w.writeframes(data)

    @classmethod
    def _drone_unit(cls):
        """one seamless loop: integer cycles per loop for every partial, noise crossfaded at the seam"""
        rate, secs = cls.RATE, cls.UNIT_SECS
        n = int(rate * secs); two_pi = 2 * math.pi
        cyc = lambda f: round(f * secs) / secs            # snap to a whole number of cycles per loop
        f1, f2, f3, f4 = cyc(55.0), cyc(55.6), cyc(110.4), cyc(82.4)
        # noise: generate a pre-roll so the tail can be blended into what leads into sample 0
        F = int(rate * 1.0)
        lp = 0.0; noise = []
        for i in range(F + n):
            lp += ((random.random() * 2 - 1) - lp) * 0.02
            noise.append(lp)
        pre, main = noise[:F], noise[F:]
        for j in range(F):
            a = j / F
            main[n - F + j] = main[n - F + j] * (1 - a) + pre[j] * a
        out = []
        for i in range(n):
            t = i / rate
            lfo = 0.85 + 0.15 * math.sin(two_pi * t / secs)  # one slow breath per loop, so it wraps
            s = (0.22 * math.sin(two_pi * f1 * t) + 0.18 * math.sin(two_pi * f2 * t)
                 + 0.10 * math.sin(two_pi * f3 * t) + 0.06 * math.sin(two_pi * f4 * t + math.sin(two_pi * t / secs)))
            out.append((s + main[i] * 0.9) * lfo * cls.DRONE_LEVEL)
        return out

    def _build_drone(self):
        unit = self._to_int16(self._drone_unit())
        self._write_wav_bytes(self.loop_path, unit.tobytes())        # used by winsound, which loops natively
        long = unit * self.TILES                                       # used by the process players, crossfaded
        F = int(self.RATE * self.XFADE)
        for j in range(F):
            a = j / F
            long[j] = int(long[j] * a)
            long[len(long) - 1 - j] = int(long[len(long) - 1 - j] * a)
        self._write_wav_bytes(self.long_path, long.tobytes())

    @classmethod
    def _horn_samples(cls):
        rate, secs = cls.RATE, 3.2
        n = int(rate * secs); out = []; two_pi = 2 * math.pi
        for i in range(n):
            t = i / rate
            f = 233.1 * (1 + 0.012 * math.sin(two_pi * 5.5 * t)) * (1 + 0.04 * (t > 2.1))
            s = sum(math.sin(two_pi * f * h * t) / h for h in range(1, 9))
            env = min(1, t * 8) * (1 if t < 2.4 else max(0, 1 - (t - 2.4) / 0.8))
            out.append(s * 0.18 * env * cls.HORN_LEVEL / 0.6)
        return out

    # --- playback
    def _play_cmd(self, path):
        return {'afplay': ['afplay', path], 'paplay': ['paplay', path], 'aplay': ['aplay', '-q', path],
                'ffplay': ['ffplay', '-nodisp', '-autoexit', '-loglevel', 'quiet', path]}[self.player]

    def _spawn(self, path):
        try:
            return subprocess.Popen(self._play_cmd(path), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            return None

    def _loop(self):
        olds = []
        cur = self._spawn(self.long_path); started = time.time()
        while not self._stop.is_set():
            if time.time() - started >= self.long_secs - self.XFADE:
                olds.append(cur); cur = self._spawn(self.long_path); started = time.time()
            olds = [p for p in olds if p is not None and p.poll() is None]
            time.sleep(0.05)
        for p in olds + [cur]:
            if p is not None and p.poll() is None:
                p.terminate()

    def drone(self, on=True):
        if not self.player:
            return
        self._drone_on = on
        if self.player == 'winsound':
            import winsound
            if on:
                winsound.PlaySound(self.loop_path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP)
            else:
                winsound.PlaySound(None, winsound.SND_PURGE)
            return
        if on and self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True); self._thread.start()
        elif not on and self._thread is not None:
            self._stop.set(); self._thread.join(timeout=1.0); self._thread = None

    def horn(self):
        if not self.player:
            return
        if self.player == 'winsound':
            # winsound plays one sound at a time: the horn interrupts the drone, so bring the drone back after
            import winsound
            winsound.PlaySound(self.horn_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            threading.Timer(3.4, lambda: self.drone(True) if self._drone_on else None).start()
            return
        p = self._spawn(self.horn_path)
        if p:
            self.procs.append(p)

    def speak(self, text, prefs, rate=150):
        if not self.speaker:
            return
        sp = self.speaker
        if sp == 'say':
            cmd = ['say', '-r', str(rate)]
            v = self.pick_voice(prefs)
            if v:
                cmd += ['-v', v]
            cmd.append(text)
        elif sp in ('espeak-ng', 'espeak'):
            cmd = [sp, '-s', str(rate), '-p', '25', text]
        elif sp == 'spd-say':
            cmd = ['spd-say', '-r', str(max(-100, min(100, (rate - 150) * 2))), text]
        elif sp == 'powershell':
            r = -4 if rate < 120 else -2 if rate < 150 else 0
            script = "$v=New-Object -ComObject SAPI.SpVoice;$v.Rate=%d;[void]$v.Speak('%s')" % (r, text.replace("'", "''"))
            cmd = ['powershell', '-NoProfile', '-NonInteractive', '-Command', script]
        else:
            return
        try:
            self.procs.append(subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        except Exception:
            pass

    def hush(self):
        for p in self.procs:
            if p.poll() is None:
                p.terminate()
        self.procs = []

    def stop_all(self):
        self.drone(False); self.hush()


# ----------------------------------------------------------------------------
# the screen
# ----------------------------------------------------------------------------
class Quit(Exception):
    pass


class UI:
    MARGIN = 4

    def __init__(self, scr, audio, fast=False, autopilot=False):
        self.scr, self.audio, self.fast, self.auto = scr, audio, fast, autopilot
        curses.curs_set(0); scr.nodelay(True); scr.keypad(True)
        try:
            curses.start_color(); curses.use_default_colors()
            for i, c in enumerate([curses.COLOR_RED, curses.COLOR_BLUE, curses.COLOR_YELLOW, curses.COLOR_WHITE,
                                   curses.COLOR_MAGENTA, curses.COLOR_GREEN, curses.COLOR_CYAN], 1):
                curses.init_pair(i, c, -1)
        except Exception:
            pass
        self.page = []
        self.skip = fast
        self.vars = {}
        self.log = []
        self.t0 = time.time()
        self.canned = ['nobody', 'I don\'t know', 'the house', 'you did it', 'laurent is dead', '']

    # --- colors
    def color(self, name):
        idx = {'red': 1, 'blue': 2, 'yellow': 3, 'white': 4, 'magenta': 5, 'green': 6, 'cyan': 7}.get(name)
        return curses.color_pair(idx) if idx else 0

    # --- drawing
    def width(self):
        return max(20, self.scr.getmaxyx()[1] - self.MARGIN * 2)

    def render(self, corrupt=0.0, shift=0, invert=False):
        h, w = self.scr.getmaxyx()
        self.scr.erase()
        avail = max(1, h - 2)
        lines = self.page[-avail:]
        for i, (t, a) in enumerate(lines):
            x = self.MARGIN + shift
            if corrupt:
                t = ''.join(random.choice(GLYPHS) if random.random() < corrupt else ch for ch in t)
            if invert:
                a |= curses.A_REVERSE
            try:
                self.scr.addstr(i + 1, max(0, x), t[:max(0, w - x - 1)], a)
            except curses.error:
                pass
        self.scr.refresh()

    def fmt(self, text):
        class D(dict):
            def __missing__(self, k):
                return '{' + k + '}'
        return text.format_map(D(self.vars))

    def _sleep(self, t):
        if self.skip or self.auto:
            return
        end = time.time() + t
        while time.time() < end:
            k = self.scr.getch()
            if k == curses.KEY_RESIZE:
                self.render(); continue
            if k != -1:
                self.skip = True; return
            time.sleep(0.01)

    def beat(self, t=1.0):
        self.render(); self._sleep(t)

    def blank(self, n=1):
        for _ in range(n):
            self.page.append(['', 0])
        self.render()

    def write(self, text, color=None, speed=1.0, pause=0.7, bold=False, dim=False, italic=False):
        attr = self.color(color) if color else 0
        if bold: attr |= curses.A_BOLD
        if dim: attr |= curses.A_DIM
        if italic: attr |= getattr(curses, 'A_ITALIC', 0)
        text = self.fmt(text)
        for para in text.split('\n'):
            for line in (textwrap.wrap(para, self.width()) or ['']):
                self.page.append(['', attr])
                for ch in line:
                    self.page[-1][0] += ch
                    if not (self.skip or self.auto):
                        self.render()
                        d = 0.028
                        if ch in ',;': d = 0.16
                        elif ch in '.!?': d = 0.38
                        elif ch in '—:': d = 0.3
                        self._sleep(d * speed)
                self.render()
        if pause:
            self._sleep(pause)

    def clear(self):
        self.page = []
        self.render()

    def card(self, lines, color=None, hold=1.6, bold=True, wait_key=False):
        h, w = self.scr.getmaxyx()
        attr = (self.color(color) if color else 0) | (curses.A_BOLD if bold else 0)
        self.scr.erase()
        top = max(0, h // 2 - len(lines) // 2)
        for i, l in enumerate(lines):
            l = self.fmt(l)
            try:
                self.scr.addstr(top + i, max(0, (w - len(l)) // 2), l[:w - 1], attr if i == 0 else self.color(color) if color else 0)
            except curses.error:
                pass
        self.scr.refresh()
        if wait_key:
            self.wait_key()
        else:
            self._sleep(hold)

    def wait_key(self):
        if self.auto:
            return
        self.skip = self.fast
        curses.flushinp()
        while True:
            k = self.scr.getch()
            if k == 3:
                raise Quit()
            if k not in (-1, curses.KEY_RESIZE):
                return k
            time.sleep(0.02)

    def rest(self):
        """a stylistic no-op: the picture holds until you touch it. no prompt."""
        self.skip = self.fast
        self.page.append(['', 0]); self.page.append(['', curses.A_DIM])
        for ch in '\u00b7 \u00b7 \u00b7':
            self.page[-1][0] += ch
            self.render()
            if not self.auto:
                self._sleep(0.22)
        self.render()
        self.wait_key()

    # --- effects
    def glitch(self, dur=1.0, intensity=0.35):
        if self.auto:
            dur = 0.05
        end = time.time() + dur
        while time.time() < end:
            self.render(corrupt=random.random() * intensity, shift=random.choice([0, 0, 0, 1, -1, 2, -3]))
            time.sleep(0.045)
        self.render()

    def flicker(self, n=5):
        if self.auto:
            n = 1
        for i in range(n):
            self.render(invert=True); time.sleep(random.uniform(0.02, 0.09))
            self.render(); time.sleep(random.uniform(0.05, 0.25))

    def static(self, dur=1.0):
        if self.auto:
            dur = 0.05
        h, w = self.scr.getmaxyx()
        end = time.time() + dur
        while time.time() < end:
            self.scr.erase()
            for y in range(h - 1):
                try:
                    self.scr.addstr(y, 0, ''.join(random.choice(' .:░▒▓') for _ in range(w - 1)), curses.A_DIM)
                except curses.error:
                    pass
            self.scr.refresh(); time.sleep(0.05)
        self.render()

    def waveform(self, dur, label='', amp=1.0, dying=False):
        """a band that is not there"""
        if self.auto:
            dur = 0.06
        h, w = self.scr.getmaxyx()
        end = time.time() + dur; t0 = time.time()
        while time.time() < end:
            t = time.time() - t0
            a = amp * (max(0, 1 - t / dur) if dying else 1)
            self.render()
            y0 = h - 5
            for x in range(1, w - 1):
                v = (math.sin(x * 0.22 + t * 9) * 0.6 + math.sin(x * 0.07 - t * 4) * 0.4) * a
                y = int(y0 + v * 2.5)
                try:
                    self.scr.addstr(max(1, min(h - 2, y)), x, '~' if abs(v) < 0.5 else '≈', self.color('magenta') | curses.A_BOLD)
                except curses.error:
                    pass
            if label:
                try:
                    self.scr.addstr(h - 2, max(0, (w - len(label)) // 2), label, self.color('magenta'))
                except curses.error:
                    pass
            self.scr.refresh()
            time.sleep(0.05)
        self.render()

    # --- interaction
    def _getkey(self):
        while True:
            k = self.scr.getch()
            if k == 3:
                raise Quit()
            if k != -1:
                return k
            time.sleep(0.015)

    def _log(self, prompt, value):
        self.log.append([round(time.time() - self.t0, 1), prompt, value])

    def choose(self, options, prompt=None):
        options = [self.fmt(o) for o in options]
        self.skip = self.fast
        curses.flushinp()
        if prompt:
            self.write(prompt, dim=True, pause=0.2)
        self.page.append(['', 0])
        base = len(self.page)
        for o in options:
            self.page.append(['', 0])
        sel = 0
        if self.auto:
            sel = random.randrange(len(options))
        else:
            while True:
                for i, o in enumerate(options):
                    self.page[base + i] = [('  > ' if i == sel else '    ') + o,
                                           (self.color('yellow') | curses.A_BOLD) if i == sel else curses.A_DIM]
                self.render()
                k = self._getkey()
                if k in (curses.KEY_UP, ord('k'), ord('w')): sel = (sel - 1) % len(options)
                elif k in (curses.KEY_DOWN, ord('j'), ord('s'), 9): sel = (sel + 1) % len(options)
                elif ord('1') <= k <= ord('9') and k - ord('1') < len(options): sel = k - ord('1'); break
                elif k in (10, 13, curses.KEY_ENTER, ord(' ')): break
        del self.page[base - 1:]
        self.page.append(['', 0]); self.page.append(['  > ' + options[sel], self.color('yellow')])
        self.render()
        self._log(prompt or '|'.join(options), options[sel])
        self.skip = self.fast
        return sel

    def ask(self, prompt, maxlen=70):
        prompt = self.fmt(prompt)
        self.skip = self.fast
        curses.flushinp()
        self.page.append(['', 0])
        self.page.append([prompt + ' ', self.color('yellow')])
        buf = ''
        if self.auto:
            buf = random.choice(self.canned)
        else:
            while True:
                self.page[-1] = [prompt + ' ' + buf + '_', self.color('yellow')]
                self.render()
                k = self._getkey()
                if k in (10, 13, curses.KEY_ENTER): break
                if k in (curses.KEY_BACKSPACE, 127, 8): buf = buf[:-1]
                elif 32 <= k <= 126 and len(buf) < maxlen: buf += chr(k)
        buf = buf.strip()
        self.page[-1] = [prompt + ' ' + buf, self.color('yellow')]
        self.render()
        self._log(prompt, buf)
        self.skip = self.fast
        return buf


# ----------------------------------------------------------------------------
# the picture
# ----------------------------------------------------------------------------
class Picture:
    def __init__(self, ui, audio, mem, loops=None):
        self.ui, self.au, self.mem = ui, audio, mem
        self.S = {}
        self.loops = loops  # autopilot: how many times through before stopping
        self.loop_count = 0

    # --- voices
    def _w(self, t, kw, **defaults):
        defaults.update(kw); self.ui.write(t, **defaults)
    def n(self, t, **k):  self.ui.write(t, **k)
    def pale(self, t, speak=True, **k):
        self._w(t, k, color='red', bold=True, speed=1.4)
        if speak:
            self.au.speak(self.ui.fmt(t), ['Whisper', 'Fred', 'Daniel', 'Alex'], 135)
    def cowboy(self, t, speak=True, **k):
        self._w(t, k, color='yellow', speed=1.5)
        if speak:
            self.au.speak(self.ui.fmt(t), ['Grandpa (English (US))', 'Grandpa', 'Fred', 'Alex'], 110)
    def doc(self, t, **k):   self._w(t, k, color='green', speed=0.8)
    def mc(self, t, speak=False, **k):
        self._w(t, k, color='magenta', bold=True)
        if speak:
            self.au.speak(self.ui.fmt(t), ['Bad News', 'Fred', 'Daniel'], 95)
    def her(self, t, **k):   self._w(t, k, color='cyan')
    def boss(self, t, **k):  self._w(t, k, color='white', bold=True)
    def quiet(self, t, **k): self._w(t, k, dim=True, speed=1.3, pause=1.0)

    def cut(self):
        self.ui.rest(); self.ui.clear()

    def set_name(self, name):
        name = ''.join(ch for ch in name if ch.isprintable())[:40] or 'Nobody'
        self.S['real_name'] = name
        self.ui.vars['name'] = name

    def lose_name(self):
        self.S['lost_name'] = True
        self.ui.vars['name'] = '—'

    # ------------------------------------------------------------------ title
    def title(self):
        ui, mem = self.ui, self.mem
        ui.vars.update({'user': getpass.getuser(), 'runs': mem['runs'], 'eddie': 'Eddie'})
        self.au.drone(True)
        ui.card(['N O   H A Y   B A N D A', '', 'a terminal picture in three acts', '', '',
                 'any key hurries the text to the next choice', 'ctrl-c leaves', '', 'press a key'],
                color='white', wait_key=True)
        ui.clear()
        if mem['runs'] > 0 and mem['names']:
            last = mem['names'][-1]
            when = mem.get('last_played') or 'some time ago'
            ui.vars['when'] = when
            self.quiet('You have been here before.')
            self.quiet('{runs} time{s}. The last time was {when}.'.replace('{s}', 's' if mem['runs'] != 1 else ''))
            self.quiet('You said your name was ' + esc(last) + '.')
            c = ui.choose(['It still is.', 'That was someone else.'])
            if c == 0:
                self.set_name(last)
            else:
                self.S['denied_name'] = True
                self.set_name(ui.ask('Then what do they call you?') or 'Nobody')
        else:
            self.quiet('Before we begin.')
            self.set_name(ui.ask('What do they call you?') or 'Nobody')
        if self.S['real_name'].lower() == getpass.getuser().lower():
            self.S['true_name'] = True
        self.S['hour'] = datetime.datetime.now().hour
        ui.blank(); ui.beat(0.8)

    # ------------------------------------------------------------------ act I
    def act_one(self):
        ui, S = self.ui, self.S
        self.cut()
        ui.card(['I.', '', 'THE ROAD ABOVE THE CITY'], color='white', hold=2.2)
        ui.clear()
        self.n('It is night. It is always night here, which should bother you more than it does.')
        self.n('You are driving on the road above the city. The road has no lines painted on it. The headlights make a small room of light and you push the room forward.')
        self.n('There is a man in the passenger seat.', pause=1.4)
        self.n('You do not remember stopping.', pause=1.6)
        ui.flicker(3)
        self.n('He has no eyebrows. His face is the color of a thing that has never been outside. He is smiling the way a photograph of a smile smiles.')
        self.pale('We\'ve met before.')
        c = ui.choose(['"I don\'t think so."', '"Where?"', 'Say nothing. Drive.'])
        if c == 0:
            self.pale('Sure we have.')
        elif c == 1:
            self.pale('At your house.')
        else:
            S['silent_car'] = True
            self.pale('You don\'t have to talk. I\'ll talk.')
        self.pale('At your house. Don\'t you remember? You invited me.')
        self.pale('As a matter of fact, I\'m there right now.')
        ui.beat(1.2)
        self.n('He takes a phone from his coat and holds it out. It is already ringing. It is ringing from both ends.')
        self.pale('Call me.')
        c = ui.choose(['Take the phone.', 'Don\'t.'])
        if c == 1:
            S['refused_phone'] = True
            self.n('You don\'t. The ringing does not care. It gets louder in a way that is not about volume.')
            self.n('Your hand takes the phone. You watch it do this.')
        self.n('Ring. Ring.', pause=1.2)
        self.n('Someone picks up at your house.', pause=1.4)
        self.phone_voice()
        self.pale('How\'d I get in your house?', speak=False)
        self.pale('You invited me. It is not my custom to go where I am not wanted.')
        ui.glitch(0.6, 0.2)
        self.n('The passenger seat is empty. The air smells like a struck match and the inside of a television.')
        self.n('On the seat where he was: a photograph. A blue box. Nothing else in the frame. The photograph is warm.', pause=1.2)
        c = ui.choose(['Drive home.', 'Drive to Pinky\'s. The diner.', 'Keep driving. Just keep driving.'], 'Where to?')
        if c == 2:
            S['kept_driving'] = True
            self.n('You keep driving.')
            self.n('You pass a billboard. SILENCIO, it says. Nothing else.')
            self.n('You pass the billboard.')
            self.n('You pass the billboard.', pause=1.3)
            self.n('The third time, the car makes the turn for you. It is a polite car. It takes you home.')
            self.house()
        elif c == 1:
            S['diner_first'] = True
            self.diner()
            self.house()
        else:
            self.house()
        if not S.get('diner_first'):
            self.diner()
        self.silencio()

    def phone_voice(self):
        ui, S, mem = self.ui, self.S, self.mem
        msg = mem.get('last_message')
        if msg:
            self.n('A voice. Your voice. It is tired in a way you are not yet.')
            ui.write('"' + esc(msg) + '"', color='red', speed=1.6, pause=1.5)
            self.au.speak(msg, ['Whisper', 'Fred'], 120)
            S['heard_message'] = True
        else:
            self.n('Static. Not the static of a bad line. The static of a good line to a bad place.')
            ui.write('"...is dead."', color='red', speed=1.8, pause=1.5)
            self.au.speak('is dead', ['Whisper', 'Fred'], 110)
        self.n('Click.')
        c = ui.choose(['"Who is this?"', 'Hang up.', 'Listen to the dial tone for a while.'])
        if c == 0:
            S['asked_who'] = True
            self.pale('You know who.')
        elif c == 2:
            S['listened'] = True
            self.n('The dial tone has a melody in it if you listen long enough. You listen long enough. You will hear it again.')

    # ------------------------------------------------------------------ house
    def house(self):
        ui, S, mem = self.ui, self.S, self.mem
        self.cut()
        ui.card(['THE HOUSE'], color='white', hold=1.6)
        ui.clear()
        self.n('Your house is the color houses are at three in the morning. Every window is a different kind of dark.')
        self.n('On the porch there is a manila envelope. Nobody\'s handwriting is on it.')
        c = ui.choose(['Open it.', 'Leave it. Go inside.'])
        if c == 0:
            S['took_tape'] = True
            self.n('A videotape. You still have a machine that plays these. You have never been sure why.')
            self.n('You go inside and you put it in the machine before you turn on a light.')
            ui.static(1.4)
            if mem['runs'] > 0 and mem.get('last_inputs'):
                self.n('The tape shows a screen. A terminal. Someone is typing.', pause=1.0)
                self.n('The timestamp in the corner reads ' + (mem.get('last_played') or 'now') + '.')
                for el, prompt, val in mem['last_inputs'][:9]:
                    ui.write(esc('   [%6.1fs]  %s   %s' % (el, (prompt or '')[:38], ('"' + val + '"') if val else '(nothing)')), dim=True, speed=0.5, pause=0.15)
                self.n('The person typing has your hands.', pause=1.6)
            else:
                self.n('The tape shows your house. From the street.')
                self.n('Then from the lawn.')
                self.n('Then from the hallway outside your bedroom.')
                self.n('Then you. Asleep. Breathing. The timestamp in the corner is now.', pause=1.6)
            ui.static(0.5)
        else:
            self.n('You step over it. It is still there when you look back, which is normal, and it is a little closer to the door, which is not.')
        self.n('In the kitchen, a woman is sitting at your table, in your robe, drinking from your cup.')
        self.her('You\'re home late, {name}.')
        ui.beat(1.2)
        self.her('That\'s funny.', pause=1.2)
        self.her('I\'m {name}.')
        c = ui.choose(['"Who are you?"', '"Get out of my house."', '"I know."'])
        if c == 0:
            self.her('I just told you. Weren\'t you listening? You never listen. That\'s the one thing everybody says about us.')
        elif c == 1:
            S['hostile_double'] = True
            self.n('She looks at the cup. She looks at the robe.')
            self.her('Whose house?')
        else:
            S['played_along'] = True
            self.her('Good. Then you know it\'s going to be fine. It\'s always fine. It\'s the same every time.')
        self.her('The Doctor said you\'d come back wrong. He\'s at Pinky\'s. He\'s always at Pinky\'s.')
        self.n('The phone on the wall rings. It is the same ring as the one in the car. It is the same ring as all of them.')
        c = ui.choose(['Answer it.', 'Let it ring.'])
        if c == 0:
            S['answered_house'] = True
            self.pale('See?')
            self.n('Click.')
        else:
            self.n('It rings nine times and stops. She counts them on her fingers, and then on yours.')

    # ------------------------------------------------------------------ diner
    def diner(self):
        ui, S = self.ui, self.S
        self.cut()
        ui.card(['PINKY\'S'], color='white', hold=1.6)
        ui.clear()
        self.n('Pinky\'s is the kind of diner that is open because closing would require a decision.')
        self.n('The coffee is the same color as the counter. The counter is the same color as the man behind it. He does not look up. He has looked up before and it did not help.')
        self.n('In the back booth: a man in a lab coat over a bathrobe. There is a flask on the table and it is the cleanest thing in the room. He waves you over with two fingers.')
        self.doc('Kid. Sit. Don\'t— don\'t order the coffee, the coffee is a loop, it\'s the same cup, you\'d be drinking yourself.')
        self.doc('You want to know about the box. Everybody wants to know about the box. Nobody wants to know about the box.')
        c = ui.choose(['"What is it?"', '"Who are you?"', '"Where\'s the key?"'])
        if c == 1:
            S['asked_doctor'] = True
            self.doc('I\'m the guy who built it, which makes me the guy who gets to sit in the back of a diner for the rest of forever. I\'m the Doctor. Not that kind. The other kind. The kind that burps.')
        elif c == 2:
            self.doc('Straight to the key. I like that. I hate it, but I like it.')
        self.doc('Okay. Infinite realities. Everybody knows that now, it\'s on t-shirts. Here\'s the part they leave off the t-shirt:')
        self.doc('In almost all of them you are not the main character. You\'re set dressing. You\'re the guy in the booth. You\'re the thing in the box.')
        self.doc('The box is a portal gun for people who can\'t be trusted with a portal gun. One trip. One other you. You open it, you get to be the one whose life is going fine.')
        self.doc('The catch— there\'s always a catch, the catch is the product— the catch is somebody has to stay behind and hold the bag. And the bag is you. And also, the bag is you.', pause=1.2)
        self.n('He drinks. The flask is still full afterward. He notices you noticing.')
        self.doc('Yeah. Don\'t think about it. Thinking about it is how it gets you.')
        self.doc('The key\'s not here. The key\'s at Silencio. There\'s no band. Go anyway.')
        ui.beat(1.0)
        self.n('In the next booth, a man is sweating through a good shirt. He leans over the seat-back without quite looking at you.')
        ui.write('"I\'ve had this dream twice now. This place. This booth. There\'s a man out back. Behind the dumpster. He\'s the one doing it. All of it. I just want to see him. I just want to see his face, and then I\'ll know."', color='white', dim=True, speed=1.1)
        self.n('He is already standing. He wants you to come. He very much does not want to go alone.')
        c = ui.choose(['Go out back with him.', 'Stay in the booth.'])
        if c == 0:
            S['went_out_back'] = True
            self.n('You walk around the side of the building. It takes longer than the building is.')
            self.n('The dumpster. The wall. The wall. The dumpster.', speed=1.6, pause=1.4)
            self.n('And he is there.', pause=1.4)
            self.n('He has the face you would have if nobody had ever been kind to you. Not angry. Just the face, with all of that taken out of it.')
            ui.flicker(7)
            ui.static(2.0)
            self.n('You are in the booth. The Doctor is wiping the table with a napkin that is not getting cleaner.')
            self.doc('Told you. Not in so many words, but I told you.')
            self.doc('You know what you left back there? Your name. Check your pockets. Go on.')
            self.lose_name()
            self.n('You check your pockets.', pause=1.4)
            self.n('You are called {name} now. It is quieter.')
        else:
            self.n('He goes alone. You hear him walk around the side of the building. You do not hear him walk back.')
            self.n('The Doctor does not look up from the table.')
            self.doc('Smart. Dumb, but smart.')

    # ------------------------------------------------------------------ silencio
    def silencio(self):
        ui, S = self.ui, self.S
        self.cut()
        ui.card(['SILENCIO'], color='magenta', hold=1.8)
        ui.clear()
        self.n('Club Silencio has no address. You know where it is the way you know where your teeth are.')
        self.n('Red curtains. Blue light. Forty empty seats and you. A man walks onto the stage with the confidence of someone who has never once been contradicted by a room.')
        self.mc('No hay banda!', speak=True)
        self.mc('There is no band.')
        self.mc('And yet.', pause=1.3)
        self.mc('We hear a band.')
        self.au.horn()
        self.n('A trumpet. A man in the corner, cheeks full, eyes closed, playing it.')
        ui.waveform(2.6, '~ ~ ~   t r u m p e t   ~ ~ ~')
        self.n('He lowers the trumpet. He holds it at his side like a thing he found.')
        self.n('The trumpet continues.', pause=0.8)
        ui.waveform(2.4, '~ ~ ~   t r u m p e t   ~ ~ ~')
        self.mc('It is all. A tape. Recording.', speak=True)
        self.mc('Silencio.')
        self.n('A woman walks to the microphone. She has been crying for so long that it has become a kind of makeup.')
        self.n('She sings.', pause=0.9)
        for line in ['I was a house with the lights on,', 'and you were the road going past,', 'and the road did not stop, and the lights did not stop,',
                     'and nothing we did was the last.']:
            ui.write('       ' + line, color='blue', speed=1.3, pause=0.5)
        ui.waveform(1.8, '', amp=0.6)
        self.n('She falls. The way a coat falls off a hook. Two men in dark suits carry her into the wings.')
        self.n('She is still singing.', pause=1.0)
        for line in ['and nothing we did was the last,', 'and nothing we did was the last.']:
            ui.write('       ' + line, color='blue', speed=1.5, pause=0.6)
        ui.waveform(2.0, '', amp=0.5, dying=True)
        ui.beat(1.2)
        self.n('In your seat, in your lap, where there was nothing: the woman with blue hair from the balcony is standing beside you, and she is holding out two things.')
        self.n('A blue box. A blue key. She shakes her head when you reach for both.')
        c = ui.choose(['Take the box.', 'Take the key.'])
        S['took'] = 'box' if c == 0 else 'key'
        self.n('She takes the other one away with her. She does not walk; she is simply further away each time you look.')
        ui.beat(1.0)
        self.n('Your head hurts.', pause=1.0)
        self.n('It hurts in a specific place, like a word you can\'t remember.', pause=1.4)
        self.swap()

    # ------------------------------------------------------------------ swap
    def swap(self):
        ui, S = self.ui, self.S
        name = S['real_name']
        ui.blank()
        ui.write(esc(name.upper()), color='red', bold=True, speed=2.0, pause=0.4)
        ui.glitch(1.2, 0.5)
        if True:
            target = 'EDDIE'
            cur = list(name.upper().ljust(len(target))[:max(len(target), len(name))])
            for step in range(14):
                for i in range(len(cur)):
                    if random.random() < 0.35:
                        cur[i] = random.choice(GLYPHS + 'ABCDEFGHIJKLMNOPQRSTUVWXYZ')
                    if step > 8 and i < len(target) and random.random() < 0.5:
                        cur[i] = target[i]
                    if step > 8 and i >= len(target):
                        cur[i] = ' '
                ui.page[-1] = [''.join(cur), ui.color('red') | curses.A_BOLD]
                ui.render(corrupt=0.1 * (14 - step) / 14, shift=random.choice([0, 1, -1]))
                time.sleep(0.005 if ui.auto else 0.12)
            ui.page[-1] = ['EDDIE', ui.color('red') | curses.A_BOLD]
        ui.rest()
        ui.static(1.6)
        ui.clear()
        ui.vars['name'] = name if not S.get('lost_name') else '—'
        ui.vars['me'] = 'Eddie'
        self.n('Concrete. A cot. A light that is on for its own reasons.')
        self.n('Two guards look through the slot in the door. They look for a long time.')
        ui.write('"That\'s not him."', dim=True)
        ui.write('"Then who is it?"', dim=True, pause=1.2)
        ui.write('"That\'s Eddie. Eddie Vale. Works at the garage on Franklin. He\'s twenty-three. His parents are nice."', dim=True)
        ui.write('"So where\'s the other one?"', dim=True)
        ui.write('"There\'s no room for another one. It\'s a cell."', dim=True, pause=1.4)
        self.n('They let you go. You are not {name}. You have never heard of {name}. You are Eddie, and you have a headache, and your parents are waiting in a car outside, and they are nice.')
        self.n('You do not remember the thing you do not remember.', pause=1.5)

    # ------------------------------------------------------------------ act II
    def act_two(self):
        ui, S = self.ui, self.S
        self.cut()
        ui.card(['II.', '', 'THE GARAGE ON FRANKLIN'], color='white', hold=2.2)
        ui.clear()
        self.n('The garage smells like the inside of a struck match. You know every tool in it by the sound it makes when it is dropped.')
        self.n('Mr. Laurent comes in the way weather comes in. He is big and friendly and he has never once needed to raise his voice, and everyone here knows what that costs.')
        self.boss('Eddie! My guy. My guy.')
        self.boss('Got a car for you. Somebody drove it up on the road above the city last night. Somebody made a phone call from it.')
        self.n('It is your car. It is the car from the road. The passenger seat is empty in a way that is still warm.')
        self.boss('Listen to that engine. No— listen. There\'s a knock in it. Find me the knock.')
        c = ui.choose(['Pop the hood.', '"I don\'t hear a knock."', 'Look at the passenger seat.'])
        if c == 0:
            self.n('There is nothing under the hood that is wrong. There is a phone taped to the firewall. It is warm.')
            S['found_phone'] = True
        elif c == 1:
            self.boss('You will.')
        else:
            self.n('A photograph. A blue box. You have never seen it before. You have seen it before.')
        self.boss('Take a ride with me. I want to show you something about the rules of the road.')
        ui.beat(0.8)
        self.n('He drives like a man carrying a full glass. Behind you, a car gets close. Then closer. Its headlights fill your mirror like a room with the lights on.')
        self.boss('See that? See that? That\'s a man who\'s decided the rules don\'t apply to him.')
        self.n('He brakes. The car behind swerves into the gravel. Mr. Laurent gets out with the calm of a man going to check the mail.')
        self.n('He pulls the driver out through the window. He explains, in a voice you could set a watch by, the correct following distance in feet per ten miles per hour. He explains it with his hands.')
        c = ui.choose(['Watch.', 'Look away.', '"Mr. Laurent. Stop."'])
        if c == 0:
            S['watched'] = True
            self.n('You watch. It goes on longer than a lesson needs to. He looks up at you once, in the middle, to make sure you are learning.')
        elif c == 1:
            S['looked_away'] = True
            self.n('You look at the city instead. All of those lights, and every one of them is somebody not looking at something.')
        else:
            S['said_stop'] = True
            self.n('He stops. Not because you said it. He looks at you like you have said something in a language he almost remembers.')
            self.boss('Eddie. Eddie. That\'s not a thing you say to me. That\'s a thing somebody else says to me. Who have you been talking to?')
        self.n('Back at the garage, two detectives are waiting by the coffee machine like they were installed with it.')
        ui.write('"Eddie Vale?"', dim=True)
        ui.write('"Where were you Thursday?"', dim=True)
        c = ui.choose(['"Here. Working."', '"I don\'t remember."', '"Thursday hasn\'t happened yet."'])
        if c == 2:
            S['clever'] = True
            ui.write('"That\'s what the other one said."', dim=True, pause=1.2)
        elif c == 1:
            ui.write('"Nobody remembers. That\'s not the question."', dim=True, pause=1.0)
        self.n('The tall one reads from a notebook that is empty.')
        evid = []
        if S.get('took_tape'):
            evid.append('a videotape was removed from a porch on Sierra Bonita.')
        if S.get('went_out_back'):
            evid.append('a man went out behind Pinky\'s and came back with something missing.')
        if S.get('hostile_double'):
            evid.append('a woman was told to get out of her own house.')
        if S.get('answered_house'):
            evid.append('a telephone was answered. We have the recording. It\'s mostly one word.')
        if S.get('kept_driving'):
            evid.append('a car passed the same billboard three times, which the city considers a statement.')
        if not evid:
            evid.append('a man did almost nothing, carefully, for an entire night, which is the most suspicious thing we\'ve ever heard.')
        ui.write('"Thursday night: ' + ' Also, '.join(evid) + '"', dim=True, speed=0.9, pause=1.0)
        ui.write('"Funny thing. The man who did all that is named {name}."', dim=True, pause=1.3)
        ui.write('"You ever meet a {name}, Eddie?"', dim=True)
        c = ui.choose(['"No."', '"Yes."', '"I think I am him."'])
        if c == 1:
            S['admitted'] = True
            ui.write('"When?"', dim=True); ui.write('"...Thursday."', color='white', pause=1.0)
        elif c == 2:
            S['confessed'] = True
            ui.write('"Yeah. Everybody thinks that. It\'s the headache. It passes."', dim=True, pause=1.0)
        else:
            S['denied'] = True
            ui.write('"Okay. Okay. Say hi if you do."', dim=True, pause=1.0)
        self.n('They leave the way they came, which was at the same time as everyone else, from the beginning.')
        self.alice()

    def alice(self):
        ui, S = self.ui, self.S
        self.cut()
        self.n('After close, a woman is standing in the bay door with the sodium light behind her, so that for a moment she is only an outline and the outline is familiar.')
        self.n('Blonde now. Different coat. Same cup, somehow, in her hand.')
        self.her('You don\'t know me, Eddie.')
        self.her('But I know you.', pause=1.3)
        c = ui.choose(['"I know you."', '"What do you want?"', 'Say nothing.'])
        if c == 0:
            S['remembered_her'] = True
            self.her('No. You know the other one. He never listened either.')
        elif c == 2:
            self.her('That\'s new. The other one couldn\'t shut up.')
        other = 'key' if S['took'] == 'box' else 'box'
        self.her('There\'s a ' + other + ' in that house on Sierra Bonita. And the ' + S['took'] + ' is in your pocket. It\'s been in your pocket since you woke up. You just didn\'t have the pocket yet.')
        self.n('You check. It is there. It is warm. It has the weight of a thing that wants to be used.')
        self.her('I need you to drive me there. Then I need you to open it. Then I need you to be somebody else for a while, because I am very tired of being this.')
        c = ui.choose(['"Get in."', '"No. I\'m Eddie. Eddie\'s fine."'])
        if c == 1:
            S['refused_her'] = True
            self.her('Eddie\'s fine.', pause=1.0)
            self.her('Eddie is a nice life. Eddie\'s parents are nice.')
            self.her('I\'ll be back. Not for you. For the next one.')
            self.n('She walks out into the sodium light and the light does the rest.')
            self.n('But the ' + S['took'] + ' is still in your pocket. And it is still warm. And somebody is going to want to talk to you about that.')
        else:
            S['helped_her'] = True
            self.n('She gets in. She puts the cup on the dash. It does not slide, no matter how you take the turns.')
        self.cowboy_scene()

    # ------------------------------------------------------------------ cowboy
    def cowboy_scene(self):
        ui, S, mem = self.ui, self.S, self.mem
        self.cut()
        ui.card(['THE CORRAL'], color='yellow', hold=1.8)
        ui.clear()
        self.n('A dirt road at the top of a dirt road at the top of the city. A corral with nothing in it. A single bulb on a wire.')
        self.n('The bulb comes on.')
        ui.flicker(4)
        self.n('A man in a hat is standing where there was a fence post. He has the kind of face that is mostly hat.')
        self.cowboy('A man\'s attitude goes some ways to determinin\' how his life will be.')
        self.cowboy('Now, I been watchin\'. That\'s what I do. I watch. And I got some thoughts.')
        bad, good = [], []
        if S.get('hostile_double'): bad.append('You told a woman to get out of a house that was hers.')
        if S.get('took_tape'): bad.append('You took a tape that was left for somebody who looks like you.')
        if S.get('went_out_back'): bad.append('You went around back of a place when a feller told you what was there.')
        if S.get('watched'): bad.append('You watched a man get taught the rules of the road, and you didn\'t look away, and you didn\'t say stop.')
        if S.get('denied_name'): bad.append('You come in here sayin\' you was somebody other than who you said you was last time.')
        if S.get('denied'): bad.append('You told two men you never met a man you have been all night.')
        if S.get('said_stop'): good.append('You said stop to a man nobody says stop to.')
        if S.get('looked_away'): good.append('You looked away, which ain\'t brave, but ain\'t nothin\'.')
        if S.get('played_along'): good.append('You let a woman be who she said she was.')
        if S.get('helped_her'): good.append('You put the box in the car when she asked.')
        if S.get('confessed'): good.append('You said you might be him. Takes somethin\'. Most folks won\'t.')
        if S.get('true_name'): good.append('You give me your real name. The one the machine calls you. I appreciate that.')
        if S.get('listened'): good.append('You listened to the dial tone till you heard the song in it. Not many do.')
        for b in bad:
            self.cowboy(b, speak=False)
        if bad and good:
            self.cowboy('On the other hand.', speak=False)
        for g in good:
            self.cowboy(g, speak=False)
        if not bad and not good:
            self.cowboy('You ain\'t done much of anything, which I\'ll allow is one way to go about it.', speak=False)
        ui.beat(0.8)
        S['bad'] = len(bad); S['good'] = len(good)
        S['two_times'] = len(bad) > len(good)
        if S['two_times']:
            self.cowboy('You\'ll see me two more times if you do bad.')
            self.cowboy('You\'ll see me one more time if you do good.', speak=False)
        else:
            self.cowboy('You\'ll see me one more time if you do good.')
            self.cowboy('You\'ll see me two more times if you do bad.', speak=False)
        ui.beat(0.8)
        self.cowboy('Now. You got a choice to make, and I\'ll tell you what it is, because you been makin\' it wrong.')
        self.cowboy('There\'s a house. There\'s a box. There\'s a key. There\'s a road.', speak=False)
        self.cowboy('Go on.', speak=False)
        self.n('The bulb goes out. The man is a fence post. The fence post was always a fence post.')
        self.act_three()

    # ------------------------------------------------------------------ act III
    def act_three(self):
        ui, S = self.ui, self.S
        self.cut()
        ui.card(['III.', '', 'THE BOX'], color='blue', hold=2.2)
        ui.clear()
        self.n('The house on Sierra Bonita. Three in the morning, as always. The envelope is on the porch. It is on the porch whether or not you opened it.')
        self.n('Inside, on the kitchen table, where the cup was: the other half. The blue box and the blue key, in the same room for the first time since someone decided they shouldn\'t be.')
        if S.get('helped_her'):
            self.n('She is standing in the doorway behind you. She is not going to come any closer. That is your job.')
        self.n('You hold the key over the box. The key is warm. The box is warmer.', pause=1.3)
        c = ui.choose(['Open it.', 'Put it in the car. Drive.', 'Give it to her.' if S.get('helped_her') else 'Leave it. Stay Eddie.'], 'What do you do?')
        if c == 0:
            self.ending_box()
        elif c == 1:
            self.ending_highway()
        else:
            self.ending_eddie()

    def cowboy_returns(self):
        ui, S = self.ui, self.S
        times = 2 if S.get('two_times') else 1
        for i in range(times):
            ui.flicker(3)
            if i == 0:
                self.cowboy('Now ya seen me.' if times == 1 else 'That\'s one.')
            else:
                self.cowboy('That\'s two.')
                ui.beat(0.8)
                self.cowboy('Think about it.', speak=False)
            ui.beat(1.0)
        self.mem['cowboy_seen'] = self.mem.get('cowboy_seen', 0) + times

    def ending_box(self):
        ui, S, mem = self.ui, self.S, self.mem
        S['ending'] = 'box'
        self.n('The key turns the way a key turns in a dream: without resistance and without ever quite finishing.')
        ui.rest()
        ui.glitch(1.0, 0.3)
        ui.clear()
        self.scr_fill_blue()
        ui.clear()
        self.n('Inside the box there is a small piece of paper, folded many times. You unfold it. It takes a while. It is a record.', pause=1.2)
        self.n('It is a record of everything you have done here.', pause=1.4)
        rec = dict(mem); rec['this_run'] = {'name': S['real_name'], 'flags': sorted(k for k, v in S.items() if v is True), 'inputs': ui.log[-12:]}
        for line in json.dumps(rec, indent=1).splitlines()[:40]:
            ui.write(esc(line), color='blue', speed=0.25, pause=0.02)
        ui.beat(1.4)
        self.n('There is a folder on this computer called save. There is a file in it called memory.json. It is this piece of paper. It has been this piece of paper the whole time.', pause=1.4)
        self.n('You were the thing in the box.', pause=1.2)
        self.n('You always were.', pause=1.6)
        self.pale('Ha.')
        self.au.speak('ha ha ha', ['Whisper', 'Fred'], 90)
        self.cowboy_returns()
        self.n('The box closes itself. It is warm. It would like to be opened again, someday, by someone.')
        self.finish()

    def scr_fill_blue(self):
        h, w = self.ui.scr.getmaxyx()
        for y in range(h - 1):
            try:
                self.ui.scr.addstr(y, 0, '█' * (w - 1), self.ui.color('blue'))
            except curses.error:
                pass
        self.ui.scr.refresh(); time.sleep(0.02 if self.ui.auto else 1.8)

    def ending_eddie(self):
        ui, S = self.ui, self.S
        S['ending'] = 'eddie'
        if S.get('helped_her'):
            self.n('You hand her both. She opens it in the doorway, with her back to you, and whatever is in it is for her.')
            self.n('When she turns around, she is nobody you know, and she is relieved about it.')
        else:
            self.n('You leave them on the table, together, in the dark, where they can want each other without you.')
        self.n('You drive home. Your parents are nice. The garage opens at seven.')
        self.n('You never learn what you did. It is a fine life. The phone rings, sometimes, and you let it.', pause=1.5)
        self.cowboy_returns()
        self.n('It is a fine life.', pause=1.2)
        self.finish()

    def ending_highway(self):
        ui, S, mem = self.ui, self.S, self.mem
        S['ending'] = 'highway'
        self.n('You put them both in the car. The box on the passenger seat. The key in the box. Not turned. Not yet.')
        self.n('The road above the city. No lines painted on it. The headlights make a small room of light and you push the room forward.')
        self.n('The phone on the seat is ringing. It is ringing from both ends.', pause=1.2)
        self.n('You know who it is. It is you. At your house. The version that hasn\'t left yet. The version with a man in the passenger seat who is already smiling.', pause=1.4)
        self.n('He is about to put you on the phone with yourself. You get to decide what he hears.')
        msg = ui.ask('What do you tell him?') or DEFAULT_MESSAGE
        mem['last_message'] = msg[:120]
        self.n('You say it. You say it into a phone that is already saying it to someone.', pause=1.0)
        self.n('Somewhere behind you, a man you do not remember picking up is smiling.')
        self.cowboy_returns()
        self.n('Then your face begins to hurt. In a specific place. Like a word.', pause=1.2)
        ui.rest()
        ui.glitch(1.8, 0.6)
        self.finish(loop=True)

    # ------------------------------------------------------------------ finish
    def finish(self, loop=False):
        ui, S, mem = self.ui, self.S, self.mem
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
        mem['runs'] += 1
        mem['names'].append(S['real_name'])
        mem['names'] = mem['names'][-20:]
        mem['endings'].append(S.get('ending', 'left'))
        mem['last_inputs'] = ui.log[:30]
        mem['first_played'] = mem['first_played'] or now
        mem['last_played'] = now
        save_memory(mem)
        if loop:
            ui.static(1.2)
        else:
            ui.blank(2)
            ui.write('N O   H A Y   B A N D A', color='white', bold=True, pause=1.2)
            ui.write('press a key', dim=True, pause=0.1)
            ui.wait_key()

    def run(self):
        ui, mem = self.ui, self.mem
        self.title()
        while True:
            self.act_one()
            self.act_two()
            if self.S.get('ending') != 'highway':
                break
            # the road loops: Lost Highway. what you just said is what you are about to hear.
            self.loop_count += 1
            if self.loops is not None and self.loop_count >= self.loops:
                break
            self.S = {}
            ui.log = []; ui.t0 = time.time()
            ui.vars['runs'] = mem['runs']
            ui.clear()
            self.quiet('The road loops.')
            ui.rest()
            self.set_name(mem['names'][-1])


# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description='NO HAY BANDA, a terminal picture in three acts.')
    ap.add_argument('--silent', action='store_true', help='no drone, no voices')
    ap.add_argument('--fast', action='store_true', help='no typewriter')
    ap.add_argument('--forget', action='store_true', help='erase what it remembers about you, then exit')
    ap.add_argument('--autopilot', type=int, metavar='N', help='play itself N times with random choices (smoke test)')
    a = ap.parse_args()
    if a.forget:
        if os.path.exists(MEM_PATH):
            os.remove(MEM_PATH); print('Forgotten.')
        else:
            print('There was nothing to forget. That is its own kind of answer.')
        return
    mem = load_memory()
    audio = Audio(enabled=not a.silent and not a.autopilot)
    if not a.silent and not a.autopilot and not audio.player:
        print('   (no sound player found; the picture will be silent)'); time.sleep(1.2)

    def inner(scr):
        ui = UI(scr, audio, fast=a.fast, autopilot=bool(a.autopilot))
        pic = Picture(ui, audio, mem, loops=a.autopilot)
        try:
            if a.autopilot:
                for _ in range(a.autopilot):
                    pic.S = {}; ui.log = []; pic.loop_count = 0
                    pic.run()
            else:
                pic.run()
        except (Quit, KeyboardInterrupt):
            pass
        finally:
            audio.stop_all()
        return pic

    try:
        pic = curses.wrapper(inner)
    finally:
        audio.stop_all()
    if a.autopilot:
        m = load_memory()
        print('AUTOPILOT OK runs=%d endings=%s' % (m['runs'], m['endings'][-a.autopilot * 2:]))
    else:
        print('\n   silencio.\n')


if __name__ == '__main__':
    main()
