#!/usr/bin/env python3
"""
idm.py - open controller for iDotMatrix BLE pixel displays.

Protocol reverse-engineered from the official Android app v2.1.6
(com.tech.idotmatrix). See PROTOCOL.md for the byte-level details and where
each command lives in the decompiled source.

Requires:  pip install bleak pillow

Examples:
  python idm.py scan
  python idm.py --addr AA:BB:CC:DD:EE:FF brightness 40
  python idm.py --addr ... color 255 0 128
  python idm.py --addr ... clock --style 3 --color 0 200 255 --24h
  python idm.py --addr ... image photo.png --size 32
  python idm.py --addr ... gif anim.gif --size 32
  python idm.py --addr ... pixels 0,0=ff0000 5,5=00ff00
  python idm.py --addr ... raw 05 00 04 80 32        # send any hex bytes
"""
import argparse
import asyncio
import binascii
import io
import struct
import sys
import time
import zlib

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    sys.exit("Missing dependency: pip install bleak pillow")

SERVICE = "000000fa-0000-1000-8000-00805f9b34fb"
WRITE_CHAR = "0000fa02-0000-1000-8000-00805f9b34fb"
# Advertised manufacturer-data prefix the app filters on ("TR\x00p")
ADV_PREFIX = bytes([0x54, 0x52, 0x00, 0x70])

CHUNK_4K = 4096          # app splits uploads into 4 KB "big packets"
ACK_TIMEOUT = 5.0        # app gives up after 5 s without an ack


# ---------------------------------------------------------------- packets
def cmd(body: bytes) -> bytes:
    """Short command: [len_lo, len_hi, *body] where len includes the 2 length bytes."""
    n = len(body) + 2
    return bytes([n & 0xFF, n >> 8]) + body


def p_brightness(pct):          return cmd(bytes([0x04, 0x80, pct]))        # 5..100
def p_screen_on(on):            return cmd(bytes([0x07, 0x01, 1 if on else 0]))
def p_flip(on):                 return cmd(bytes([0x06, 0x80, 1 if on else 0]))
def p_color(r, g, b):           return cmd(bytes([0x02, 0x02, r, g, b]))
def p_speed(v):                 return cmd(bytes([0x03, 0x01, v]))
def p_diy_mode(mode):           return cmd(bytes([0x04, 0x01, mode]))        # 1=enter+clear
def p_countdown(mode, m, s):    return cmd(bytes([0x08, 0x80, mode, m, s]))  # 0 off,1 start,2 pause,3 resume
def p_chrono(mode):             return cmd(bytes([0x09, 0x80, mode]))       # 0 reset,1 start,2 pause,3 resume
def p_scoreboard(a, b):         return cmd(bytes([0x0A, 0x80]) + struct.pack("<HH", a, b))
def p_reset():                  return cmd(bytes([0x03, 0x80]))
def p_get_led_type():           return cmd(bytes([0x01, 0x80]))
def p_delete_saved():           return bytes([17, 0, 2, 1, 12, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11])


def p_sync_time():
    t = time.localtime()
    wd = t.tm_wday + 1  # app: Monday=1 .. Sunday=7
    return cmd(bytes([0x01, 0x80, t.tm_year - 2000, t.tm_mon, t.tm_mday,
                      wd, t.tm_hour, t.tm_min, t.tm_sec]))


def p_clock(style, show_date, h24, r, g, b):
    flags = style & 0x3F
    if show_date:
        flags |= 0x80
    if h24:
        flags |= 0x40
    return cmd(bytes([0x06, 0x01, flags, r, g, b]))


def p_diy_pixels(r, g, b, xys):
    """Graffiti: one colour + up to ~250 (x,y) points. Header type 05 01, no total-len."""
    body = bytes([r, g, b]) + bytes(v for xy in xys for v in xy)
    n = len(body) + 5
    return bytes([n & 0xFF, n >> 8, 0x05, 0x01, 0x00]) + body


def upload_frames(kind: int, data: bytes, slot: int = 13, hold_s: int = 0):
    """
    Build the 4 KB framed packets for image (kind=2, raw RGB) or GIF (kind=1, file bytes).
    16-byte header per 4 KB block (all little-endian):
      [len16][kind][0][0 first|2 continued][total32][crc32][hold16 seconds][slot]
    slot 12/13 = show now without saving to a playlist slot.
    """
    crc = zlib.crc32(data) & 0xFFFFFFFF
    out = []
    for i in range(0, len(data), CHUNK_4K):
        chunk = data[i:i + CHUNK_4K]
        n = len(chunk) + 16
        hdr = struct.pack("<H", n) + bytes([kind, 0, 0 if i == 0 else 2])
        hdr += struct.pack("<II", len(data), crc)
        hdr += struct.pack("<H", 0 if slot >= 12 else hold_s) + bytes([slot])
        out.append(hdr + chunk)
    return out


# ---------------------------------------------------------------- media
def load_image_rgb(path, size):
    from PIL import Image
    im = Image.open(path).convert("RGB").resize((size, size), Image.NEAREST)
    return im.tobytes()  # row-major R,G,B — matches BGRUtils.bitmap2RGB


def load_gif(path, size):
    from PIL import Image, ImageSequence
    src = Image.open(path)
    frames, durations = [], []
    for f in ImageSequence.Iterator(src):
        frames.append(f.convert("RGB").resize((size, size), Image.NEAREST))
        durations.append(f.info.get("duration", 100))
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:],
                   duration=durations, loop=0, disposal=2, optimize=False)
    return buf.getvalue()


# ---------------------------------------------------------------- transport
class Display:
    def __init__(self, addr):
        self.addr = addr
        self.client = None
        self.notes: asyncio.Queue = asyncio.Queue()

    async def __aenter__(self):
        self.client = BleakClient(self.addr)
        await self.client.connect()
        svc = self.client.services.get_service(SERVICE)
        for ch in (svc.characteristics if svc else []):
            if "notify" in ch.properties:
                await self.client.start_notify(ch, lambda _c, d: self.notes.put_nowait(bytes(d)))
        return self

    async def __aexit__(self, *a):
        await self.client.disconnect()

    @property
    def mtu_payload(self):
        return max(20, (self.client.mtu_size or 23) - 3)

    async def send(self, data: bytes):
        step = self.mtu_payload
        for i in range(0, len(data), step):
            await self.client.write_gatt_char(WRITE_CHAR, data[i:i + step], response=False)
            await asyncio.sleep(0.01)

    async def upload(self, kind, data, slot=13):
        packets = upload_frames(kind, data, slot)
        for idx, pkt in enumerate(packets):
            await self.send(pkt)
            try:
                ack = await asyncio.wait_for(self.notes.get(), ACK_TIMEOUT)
            except asyncio.TimeoutError:
                print(f"  no ack for block {idx+1}/{len(packets)}; continuing")
                continue
            status = ack[4] if len(ack) >= 5 else None
            print(f"  block {idx+1}/{len(packets)} ack={ack.hex()}")
            if status == 3:
                print("  done"); return
            if status == 2:
                sys.exit("  device reports: not enough space")
            if status == 0:
                sys.exit("  device reports: invalid transfer command")


# ---------------------------------------------------------------- CLI
async def do_scan():
    found = await BleakScanner.discover(timeout=6, return_adv=True)
    for dev, adv in found.values():
        raw = b"".join(struct.pack("<H", k) + v for k, v in adv.manufacturer_data.items())
        if ADV_PREFIX in raw or (dev.name or "").upper().startswith("IDM"):
            print(f"{dev.address}  {dev.name}  rssi={adv.rssi}  mfr={raw.hex()}")


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--addr", help="BLE address (from `scan`)")
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("scan")
    sp.add_parser("sync-time")
    sp.add_parser("info")
    s = sp.add_parser("brightness"); s.add_argument("pct", type=int)
    s = sp.add_parser("power"); s.add_argument("state", choices=["on", "off"])
    s = sp.add_parser("flip"); s.add_argument("state", choices=["on", "off"])
    s = sp.add_parser("color"); [s.add_argument(c, type=int) for c in "rgb"]
    s = sp.add_parser("clock"); s.add_argument("--style", type=int, default=0)
    s.add_argument("--color", type=int, nargs=3, default=[255, 255, 255])
    s.add_argument("--date", action="store_true"); s.add_argument("--24h", dest="h24", action="store_true")
    s = sp.add_parser("countdown"); s.add_argument("minutes", type=int); s.add_argument("seconds", type=int)
    s = sp.add_parser("score"); s.add_argument("a", type=int); s.add_argument("b", type=int)
    s = sp.add_parser("image"); s.add_argument("path"); s.add_argument("--size", type=int, default=32)
    s = sp.add_parser("gif"); s.add_argument("path"); s.add_argument("--size", type=int, default=32)
    s = sp.add_parser("pixels", help="x,y=RRGGBB ...")
    s.add_argument("points", nargs="+")
    s = sp.add_parser("raw"); s.add_argument("hex", nargs="+")
    a = ap.parse_args()

    if a.cmd == "scan":
        return await do_scan()
    if not a.addr:
        sys.exit("--addr required (run `scan` first)")

    async with Display(a.addr) as d:
        c = a.cmd
        if c == "sync-time":   await d.send(p_sync_time())
        elif c == "brightness": await d.send(p_brightness(max(5, min(100, a.pct))))
        elif c == "power":     await d.send(p_screen_on(a.state == "on"))
        elif c == "flip":      await d.send(p_flip(a.state == "on"))
        elif c == "color":     await d.send(p_color(a.r, a.g, a.b))
        elif c == "clock":     await d.send(p_clock(a.style, a.date, a.h24, *a.color))
        elif c == "countdown": await d.send(p_countdown(1, a.minutes, a.seconds))
        elif c == "score":     await d.send(p_scoreboard(a.a, a.b))
        elif c == "info":
            await d.send(p_get_led_type())
            try: print("reply:", (await asyncio.wait_for(d.notes.get(), 3)).hex())
            except asyncio.TimeoutError: print("no reply")
        elif c == "image":
            await d.send(p_diy_mode(1))
            await d.upload(2, load_image_rgb(a.path, a.size))
        elif c == "gif":
            await d.upload(1, load_gif(a.path, a.size))
        elif c == "pixels":
            await d.send(p_diy_mode(1))
            groups = {}
            for p in a.points:
                xy, col = p.split("=")
                x, y = map(int, xy.split(","))
                groups.setdefault(col.lower(), []).append((x, y))
            for col, pts in groups.items():
                r, g, b = bytes.fromhex(col)
                for i in range(0, len(pts), 100):
                    await d.send(p_diy_pixels(r, g, b, pts[i:i + 100]))
        elif c == "raw":
            await d.send(binascii.unhexlify("".join(a.hex)))
        await asyncio.sleep(0.3)


if __name__ == "__main__":
    asyncio.run(main())
