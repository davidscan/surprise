# Multiverse Janitor

A portal gun, a mop, and a quota.

Your portal gun is broken. The only way home is *through*. Every dimension you
land in rolls its own random laws of physics, and they stack the deeper you go.
Mop up enough filth, a portal opens, pick an upgrade, jump. Every 4th dimension
the Landlord shows up for the rent.

## Play

    open index.html

Single file, no dependencies, no build. Works in Safari, Chrome and Firefox.
All sound is synthesized in the browser, so there are no assets to load.

## Controls

| Key | Action |
|---|---|
| WASD / arrows | move |
| mouse | aim |
| hold click | mop (shoot) |
| Space / Shift | dash (brief invulnerability) |
| 1 / 2 / 3 or click | choose upgrade |
| P / Esc | pause |
| M | mute |
| R | restart after dying |

## What's in it

- 22 dimensional laws, rolled per dimension: Sideways Gravity, Timey Wimey
  (time only moves when you move), Lights Out, Mirror Dimension, Gravity Well,
  Dimension of Infinite Chairs, Ice, Molasses, Bouncy Castle, The Swarm,
  Goo Moon, Jelly Physics and more. Dimension 1 always gets one gentle law.
- 17 stackable upgrades, choose one of three after each dimension.
- Six enemy types plus the Landlord boss every 4th dimension.
- Combos, hearts, score multipliers, screen shake, a narrator with opinions.
- Best score and deepest dimension are saved in localStorage.

## Smoke test

Append `?autotest` to the URL. The game plays itself for 40,000 frames with
random input, forcing every law and boss, and writes the result into the page
title. From a terminal:

    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new \
      --virtual-time-budget=20000 --dump-dom "file://$PWD/index.html?autotest" \
      | grep -o '<title>[^<]*</title>'
