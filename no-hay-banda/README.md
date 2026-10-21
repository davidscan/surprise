# NO HAY BANDA

*a terminal picture in three acts*

You are driving on the road above the city. There is a man in the passenger
seat. You do not remember stopping. He says you've met before. At your house.
In fact, he's there right now. He hands you a phone. Someone picks up at your
house. It is your voice.

What that voice says is what you told it the last time you played.

## Play

    python3 nohaybanda.py

Nothing to install. Python 3 and a terminal of at least 80x24. Runs in
Terminal.app or iTerm. Headphones recommended; play it at night.

| Flag | What it does |
|---|---|
| `--silent` | no drone, no voices |
| `--fast` | no typewriter |
| `--forget` | erase what it remembers about you |

Any key hurries the text to the next choice. Arrow keys or 1-2-3 pick an
option. Ctrl-C leaves.

## Platforms

Pure Python standard library. Nothing to install on macOS or Linux.

| | sound | voices |
|---|---|---|
| macOS | `afplay` | `say` (Whisper, Grandpa, Bad News if present) |
| Linux | `paplay`, `aplay` or `ffplay`, whichever is found | `espeak-ng`, `espeak` or `spd-say` |
| Windows | `winsound`, ships with Python | built-in SAPI voice via PowerShell |

Windows also needs the curses module, which stock Python there lacks:
`pip install windows-curses`. Use Windows Terminal rather than the legacy
console for the Unicode glyphs. If no sound player is found the picture runs
silently and says so once.

## What it does to your machine

Everything is synthesized or built in. No downloads, no network.

- A low industrial drone is generated on first run as a seamless 14-second
  loop, tiled into a three-minute file. Players are crossfaded 1.5 seconds
  before each ends, so there is no gap.
- The Pale Man, the Cowboy and the MC at Club Silencio speak out loud through
  the system voice.
- It remembers you in `save/memory.json`: your name, how many times you've
  been here, what you typed, and the message you left for yourself. The
  videotape on the porch in your second run is a replay of your first. The
  phone call at the start is whatever you said at the end. One of the endings
  prints the file to you.

Nothing is written anywhere except the `save` folder next to the script.

## Three endings

There is a box. There is a key. There is a road.

One ending stays. One ending opens. One ending loops, and when it loops, it
does not go back to the title.

## Smoke test

    python3 nohaybanda.py --autopilot 10

Plays itself with random choices, no audio, effects shortened, and reports
the endings reached. Needs a real terminal (or a pty).
