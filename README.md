# Matrix Control

A browser-based controller for Bluetooth LED displays, built with Web Bluetooth. It doesn't need the vendor apps, an account or any internet connection to the vendors.

It supports two device families and detects which one you connect:

- **iDotMatrix** pixel boards (16×16 to 64×64, full colour)
- **XiaoBai LED** badges, signs and caps from the "XiaoBai LED" / `com.smallwhite.led` app (10×30, 12×24, 12×29, 12×32, 12×48, 16×16, 32×32) and the 11×11 "marquee" speakers such as the HBS613 LED

**Open it:** the GitHub Pages link for this repo, in Chrome or Edge (desktop or Android) or Bluefy (iPhone).

| Feature | iDotMatrix | XiaoBai |
|---|---|---|
| Brightness | ✓ | ✓ (6 levels) |
| Picture, colour fill, live draw | ✓ | ✓ |
| Scrolling text | ✓ (effects, saved list) | ✓ (still / left / right / up / down) |
| GIF / animation | ✓ (as-is) | ✓ (auto-resized, up to 24 frames; not 32×32 or 10×30) |
| Custom clock, live info cards, camera / screen mirroring | ✓ | ✓ |
| Music visualizer (microphone) | ✓ (as pictures) | ✓ (the badge's own bar mode; 11 bars on the 11×11 speakers) |
| Built-in clock faces, countdown / stopwatch / score | ✓ | – |
| Clock that runs without the phone | ✓ | – |

## Files

- `index.html`: the controller, a single file with no dependencies
- `idm.py`: the same protocols as a Python command-line tool (`pip install bleak pillow`)
- `PROTOCOL.md`: both protocols, reverse-engineered from iDotMatrix 2.1.6 and XiaoBai LED 1.3.51 / 1.4.02
