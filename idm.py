#!/usr/bin/env python3
"""
idm.py - open controller for two families of BLE LED displays:

  * iDotMatrix pixel boards   (app com.tech.idotmatrix 2.1.6, service 0x00fa)
  * XiaoBai LED badges/signs  (app com.smallwhite.led 1.3.51, service 0000ae00-<model>-...)

The family and, for badges, the exact model/panel size are detected on connect.
See PROTOCOL.md for the byte-level details of both.

Requires:  pip install bleak pillow

Examples (both families):
  python idm.py scan
  python idm.py --addr AA:BB:CC:DD:EE:FF brightness 40
  python idm.py --addr ... image photo.png
  python idm.py --addr ... gif anim.gif
  python idm.py --addr ... raw 55 b8 03 05 00 00 88

XiaoBai badges:
  python idm.py --addr ... text "HELLO" --dir left --speed 4
  python idm.py --addr ... sync-time

iDotMatrix only:
  python idm.py --addr ... color 255 0 128
  python idm.py --addr ... clock --style 3 --color 0 200 255 --24h
  python idm.py --addr ... image photo.png --size 32     # iDotMatrix needs the panel size
  python idm.py --addr ... pixels 0,0=ff0000 5,5=00ff00
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


# ---------------------------------------------------------------- XiaoBai badges
# (service, model, width, height, pixel format, write size) from BleClient.onServicesDiscovered
XB_MODELS = [
    ("0000ae00-0000-0000-8000-00805f9b34fb", 1, 30, 10, "rgb555", 220),
    ("0000ae00-1030-0001-8000-00805f9b34fb", 4, 30, 10, "rgb555", 220),
    ("0000ae00-1232-0000-8000-00805f9b34fb", 3, 32, 12, "rgb444", 220),
    ("0000ae00-1232-0001-8000-00805f9b34fb", 6, 32, 12, "rgb444", 220),
    ("0000ae00-1232-0002-8000-00805f9b34fb", 8, 32, 12, "rgb444", 220),
    ("0000ae00-1232-0003-8000-00805f9b34fb", 9, 32, 12, "rgb444", 220),
    ("0000ae00-1232-0004-8010-00805f9b34fb", 16, 32, 12, "rgb444", 220),
    ("0000ae00-1229-1111-8012-00805f9b36fb", 19, 29, 12, "rgb444", 220),
    ("0000ae00-1224-0100-8000-00805f9b34fb", 7, 24, 12, "mono", 220),
    ("0000ae00-1248-0000-8000-00805f9b34fb", 5, 48, 12, "mono", 220),
    ("0000ae00-1248-0100-8001-00805f9b34fb", 10, 48, 12, "mono", 220),
    ("0000ae00-1248-0100-8002-00805f9b34fb", 11, 48, 12, "mono", 220),
    ("0000ae00-1248-0100-800e-00805f9b34fb", 14, 48, 12, "mono", 220),
    ("0000ae00-1248-0100-800f-00805f9b35fb", 15, 48, 12, "mono", 220),
    ("0000ae00-1248-0100-8011-00805f9b35fb", 17, 48, 12, "mono", 220),
    ("0000ae00-1248-0100-8013-00805f9b36fb", 18, 48, 12, "mono", 220),
    ("0000ae00-1248-1111-8014-00805f9b36fb", 21, 48, 12, "mono", 220),
    ("0000ae00-1616-0001-800c-00805f9b34fb", 12, 16, 16, "rgb444", 220),
    ("0000ae00-1616-0002-800c-00805f9b34fb", 13, 16, 16, "rgb444", 220),
    ("0000ae00-1616-1111-8013-00805f9b36fb", 20, 16, 16, "rgb444", 220),
    ("0000ae00-1616-1111-8015-00805f9b36fb", 22, 16, 16, "rgb444", 220),
    ("0000ae00-1616-1111-8016-00805f9b36fb", 23, 16, 16, "rgb444", 220),
    ("0000ae00-3232-0000-8000-00805f9b34fb", 2, 32, 32, "split", 488),
    # app 1.4.02: model 41, "LED_11X11" marquee speakers such as the HBS613 LED
    ("0000ae00-1111-0002-8000-00805f9b36fb", 41, 11, 11, "rgb444", 488),
]
XB_GAP = 0.05            # the app sleeps 50 ms between writes (BleClient.TIME)
XB_DIRS = {"still": 0, "right": 1, "left": 2, "up": 3, "down": 4}


class XBModel:
    def __init__(self, svc, model, w, h, fmt, mtu):
        self.svc, self.model, self.w, self.h, self.fmt, self.mtu = svc, model, w, h, fmt, mtu
        self.write = "0000ae01" + svc[8:]
        self.frame_bytes = w * h // 8 if fmt == "mono" else w * h * 2
        self.idx = (w, h) == (11, 11)          # one frame per packet, header byte 3 = packet number
        self.chunk = self.frame_bytes if fmt == "mono" or self.idx else (768 if h == 12 else 512)
        self.can_animate = fmt in ("mono", "rgb444") and self.frame_bytes == self.chunk


def xb_encode(rgb: bytes, w: int, h: int, fmt: str) -> bytes:
    """Row-major RGB -> device pixel bytes (DiyBleUtil.SendIMG*)."""
    px = lambda x, y: rgb[(y * w + x) * 3:(y * w + x) * 3 + 3]
    if fmt == "mono":                          # rows, LSB = leftmost pixel
        out = bytearray((w * h + 7) // 8)
        for y in range(h):
            for x in range(w):
                r, g, b = px(x, y)
                if 0.299 * r + 0.587 * g + 0.114 * b > 96:
                    i = y * w + x
                    out[i >> 3] |= 1 << (i & 7)
        return bytes(out)
    out = bytearray()
    def p444(x, y):
        r, g, b = px(x, y); out.extend(((g >> 4) << 4 | b >> 4, r >> 4))
    if fmt == "rgb444":                        # column by column
        for x in range(w):
            for y in range(h): p444(x, y)
    elif fmt == "split":                       # 32x32: rows 0-15 for all columns, then rows 16-31
        for y0, y1 in ((0, h // 2), (h // 2, h)):
            for x in range(w):
                for y in range(y0, y1): p444(x, y)
    else:                                      # rgb555 (10x30)
        for x in range(w):
            for y in range(h):
                r, g, b = (c * 31 // 256 for c in px(x, y))
                out.extend((((g & 7) << 5) + b, (r << 2) + ((g & 24) >> 3)))
    return bytes(out)


def xb_data_packets(data: bytes, chunk: int, frame_byte: int = 0, idx: bool = False):
    """55 B1 <byte offset LE16> <chunk> <frame byte> 88; 11x11: 55 B1 00 <packet number> <chunk> <frame byte> 88"""
    return [bytes([0x55, 0xB1, 0, (i // chunk) & 255] if idx else [0x55, 0xB1, i & 255, (i >> 8) & 255])
            + data[i:i + chunk] + bytes([frame_byte, 0x88])
            for i in range(0, len(data), chunk)]


def xb_image_packets(m: XBModel, rgb: bytes):
    data = xb_encode(rgb, m.w, m.h, m.fmt)
    if m.fmt == "rgb555":
        l1, l2 = ((len(data) % 256) + 6) & 255, ((len(data) + 6) >> 8) & 255
        return [bytes([0x55, 0xB1, 1, 1, l1, l2]) + data + bytes([l1, l2, 0, 0x88]),
                bytes([0x55, 0xB6, 0, 0, 10, 29, 0x88])]
    return xb_data_packets(data, m.chunk, 0, m.idx) + [bytes([0x55, 0xB6, 1, 0, 0, 0, 0, 0x88])]


def xb_anim_packets(m: XBModel, frames, speed=5):
    if not m.can_animate:
        return xb_image_packets(m, frames[0])
    data = b"".join(xb_encode(f, m.w, m.h, m.fmt) for f in frames)
    sb = max(1, min(20, speed)) if m.idx else max(1, min(255, speed * 5))
    return xb_data_packets(data, m.frame_bytes, sb, m.idx) + [bytes([0x55, 0xB6, 2, 0, 0, 0, 0, 0x88])]


def crop_cols(strip: bytes, sw: int, h: int, x0: int, w: int) -> bytes:
    out = bytearray(w * h * 3)
    for y in range(h):
        for x in range(w):
            if x0 + x < sw:
                out[(y * w + x) * 3:(y * w + x) * 3 + 3] = strip[(y * sw + x0 + x) * 3:(y * sw + x0 + x) * 3 + 3]
    return bytes(out)


def xb_text_packets(m: XBModel, strip: bytes, sw: int, direction=2, speed=5):
    """Scrolling text: strip uploaded with B1, then 55 B6 03 dir speed width (TextSendUtil)."""
    h = m.h
    if m.fmt == "rgb555":
        return xb_image_packets(m, crop_cols(strip, sw, h, 0, m.w))     # 10x30 text framing not implemented
    if m.fmt == "mono":
        frames = max(1, -(-sw // m.w))
        data = b"".join(xb_encode(crop_cols(strip, sw, h, f * m.w, m.w), m.w, h, "mono") for f in range(frames))
        width = sw + 6 if m.w == 48 else frames * m.w
    elif m.idx:                                # 11x11 (getScrollData): pad to whole frames, width = columns
        if direction == 0:
            return xb_image_packets(m, crop_cols(strip, sw, h, 0, m.w))
        raw = xb_encode(strip, sw, h, "rgb444")
        data = raw + bytes(-len(raw) % m.frame_bytes or 0)
        width = len(data) // h // 2
    elif m.fmt == "rgb444":
        cols = sw + 8 if (h == 12 and direction != 0) else sw
        raw = xb_encode(crop_cols(strip, sw, h, 0, cols), cols, h, "rgb444")
        data = raw + bytes(-len(raw) % m.chunk)
        width = cols if h == 12 else len(data) // h // 2
    else:
        raw = xb_encode(strip, sw, h, "split")
        data = raw + bytes(max(m.frame_bytes - len(raw), -len(raw) % m.chunk))
        width = len(data) // h
    return xb_data_packets(data, m.chunk, 0, m.idx) + [bytes([0x55, 0xB6, 3, direction, speed, width & 255, (width >> 8) & 255, 0x88])]


def xb_brightness(pct):  return bytes([0x55, 0xB8, round(max(0, min(100, pct)) / 100 * 5), 5, 0, 0, 0x88])


def xb_time(model: int):
    t = time.localtime(); bcd = lambda n: (n // 10) * 16 + n % 10
    if model == 13:
        return bytes([0x55, 0xC1, 0x0F, t.tm_sec, t.tm_min, t.tm_hour, t.tm_mday, t.tm_mon, t.tm_year % 100, 0, 0x88])
    return bytes([0x55, 0xC1, 0x10, t.tm_sec, bcd(t.tm_min), bcd(t.tm_hour), 0, 0x88])


def render_text_strip(text, h, font_path=None):
    """Draw text at panel height; returns (row-major RGB strip, width)."""
    from PIL import Image, ImageDraw, ImageFont
    size = h + 1 if h <= 12 else int(h * 0.9)
    font = None
    for name in ([font_path] if font_path else []) + ["DejaVuSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf"]:
        try: font = ImageFont.truetype(name, size); break
        except (OSError, TypeError): pass
    font = font or ImageFont.load_default()
    w = max(1, int(ImageDraw.Draw(Image.new("L", (1, 1))).textlength(text, font=font)) + 1)
    im = Image.new("L", (w, h)); ImageDraw.Draw(im).text((0, h / 2), text, fill=255, font=font, anchor="lm")
    strip = bytes(v for p in im.getdata() for v in ((255, 255, 255) if p > 110 else (0, 0, 0)))
    return strip, w


# ---------------------------------------------------------------- media
def load_image_rgb(path, size, height=None):
    from PIL import Image, ImageOps
    w, h = size, height or size
    im = ImageOps.fit(Image.open(path).convert("RGB"), (w, h), Image.LANCZOS)
    return im.tobytes()  # row-major R,G,B — matches BGRUtils.bitmap2RGB


def load_gif_frames(path, w, h, max_frames=24):
    from PIL import Image, ImageOps, ImageSequence
    return [ImageOps.fit(f.convert("RGB"), (w, h), Image.LANCZOS).tobytes()
            for i, f in zip(range(max_frames), ImageSequence.Iterator(Image.open(path)))]


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
        self.family, self.xb = "idm", None
        svc = self.client.services.get_service(SERVICE)
        if svc is None:
            for row in XB_MODELS:
                svc = self.client.services.get_service(row[0])
                if svc is not None:
                    self.family, self.xb = "xb", XBModel(*row)
                    chars = {c.uuid.lower(): c for c in svc.characteristics}
                    if self.xb.write not in chars:     # newer models: use whatever accepts writes
                        w = next((u for u, c in chars.items() if "write-without-response" in c.properties or "write" in c.properties), None)
                        if w: self.xb.write = w
                    print(f"XiaoBai badge: model {self.xb.model}, {self.xb.w}x{self.xb.h} {self.xb.fmt}")
                    break
            else:
                raise SystemExit("not an iDotMatrix display or a XiaoBai badge")
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
        xb = self.family == "xb"
        step = min(self.xb.mtu, self.mtu_payload) if xb else self.mtu_payload
        for i in range(0, len(data), step):
            await self.client.write_gatt_char(self.xb.write if xb else WRITE_CHAR, data[i:i + step], response=False)
            await asyncio.sleep((0.1 if self.xb.idx else XB_GAP) if xb else 0.01)

    async def send_all(self, packets):
        for p in packets:
            await self.send(p)

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
        xb = next((r for r in XB_MODELS if r[0] in [u.lower() for u in adv.service_uuids]), None)
        if xb:
            print(f"{dev.address}  {dev.name}  rssi={adv.rssi}  XiaoBai model {xb[1]} {xb[2]}x{xb[3]}")
        elif ADV_PREFIX in raw or (dev.name or "").upper().startswith("IDM"):
            print(f"{dev.address}  {dev.name}  rssi={adv.rssi}  iDotMatrix mfr={raw.hex()}")


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
    s.add_argument("--speed", type=int, default=5, help="XiaoBai frame speed 1-10")
    s = sp.add_parser("text", help="XiaoBai: scrolling text"); s.add_argument("message")
    s.add_argument("--dir", choices=list(XB_DIRS), default="left")
    s.add_argument("--speed", type=int, default=5, help="1 (fast) .. 10 (slow)")
    s.add_argument("--font", help="path to a .ttf font")
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
        if d.family == "xb":
            m = d.xb
            if c == "brightness":  await d.send(xb_brightness(a.pct))
            elif c == "sync-time": await d.send(xb_time(m.model))
            elif c == "image":     await d.send_all(xb_image_packets(m, load_image_rgb(a.path, m.w, m.h)))
            elif c == "gif":       await d.send_all(xb_anim_packets(m, load_gif_frames(a.path, m.w, m.h), a.speed))
            elif c == "color":     await d.send_all(xb_image_packets(m, bytes([a.r, a.g, a.b]) * (m.w * m.h)))
            elif c == "text":
                strip, w = render_text_strip(a.message, m.h, a.font)
                await d.send_all(xb_text_packets(m, strip, w, XB_DIRS[a.dir], max(1, min(10, a.speed))))
            elif c == "raw":       await d.send(binascii.unhexlify("".join(a.hex)))
            else: sys.exit(f"'{c}' is an iDotMatrix command; this is a XiaoBai badge")
            await asyncio.sleep(0.3)
            return
        if c == "text": sys.exit("'text' is for XiaoBai badges; use the web page for iDotMatrix text")
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
