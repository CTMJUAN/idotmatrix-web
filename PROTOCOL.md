# iDotMatrix BLE protocol (from app v2.1.6)

Decompiled from `com.tech.idotmatrix` 2.1.6 (jadx 1.5.1). Paths are relative to the jadx `sources/` folder.

## Transport
- **Service** `000000fa-0000-1000-8000-00805f9b34fb`
- **Write** `0000fa02-…` (write-without-response). Notifications come back on a notify characteristic in the same service.
- The app requests MTU 512. It sends 509-byte writes when the MTU is high and 18-byte writes otherwise (`GifAgreement.getSendData`).
- **Discovery:** the advertised manufacturer data contains `54 52 00 70` ("TR\0p"). `BleConfig.matchProduct` filters on it. Inside that block, byte 5 is the panel shape and byte 10 is the PID.
- **No encryption on normal commands.** The native `libAES.so` (`csh/tiro/cc/aes`) is only used for the "find my device" `LOCATE` ping (`BleProtocol.locationDevice`).

## Short commands
Format: `[len_lo, len_hi, opcode, group, …args]`. The length counts the whole packet.

| Function | Bytes | Source |
|---|---|---|
| Sync time | `0B 00 01 80 YY MM DD wd hh mm ss` (wd: Mon=1 … Sun=7) | `BleProtocolN.synchronizedTime` |
| Get LED type / size | `04 00 01 80` | `BaseSend.getLedType` |
| Eco / night mode | `0A 00 02 80 a b c d e f` | `setEco` |
| Factory reset | `04 00 03 80` | `restDevice` |
| Brightness | `05 00 04 80 pct` (5–100) | `setLight` |
| Rotate 180° | `05 00 06 80 0/1` | `setRotate180` |
| Hourly chime switch | `05 00 07 80 0/1` | `setTimeIndicatorEnable` |
| Countdown | `07 00 08 80 mode min sec` | `setCountDown` |
| Stopwatch | `05 00 09 80 mode` | `setSecondChronograph` |
| Scoreboard | `08 00 0A 80 aLE16 bLE16` | `setScoreboard` |
| Mic type | `05 00 0B 80 n` | `setMicType` |
| Panel join / tiling | `05 00 0C 80 n` | `sendJoint` |
| Screen-on duration | `05 00 0F 80 n` (`FF` = read it back) | `setScreenLight` |
| Fill colour | `07 00 02 02 R G B` | `sendColor` |
| Set PIN | `08 00 04 02 mode p1 p2 p3` | `setPwd` |
| Verify PIN | `07 00 05 02 p1 p2 p3` | `verifyPwd` |
| Text speed | `05 00 03 01 v` | `sendSpeed` |
| DIY / graffiti mode | `05 00 04 01 m` (0 quit, 1 enter+clear, 2 quit keep, 3 enter keep) | `DiyImageFun` |
| Clock face | `08 00 06 01 flags R G B` (flags = style \| 0x80 date \| 0x40 24h) | `sendClockMode` |
| Screen on/off | `05 00 07 01 0/1` | `sendSwitchplate` |
| Delete saved material | `11 00 02 01 0C 00 01 … 0B` | `Agreement.deleteDeviceMaterial` |

## Graffiti pixels (type `05 01`)
`[len LE16] 05 01 moveType R G B x1 y1 x2 y2 …`. One colour per packet, any number of points (`SendCore.sendDiyImageData`).

## Direct frame (type `00 00`, untested)
`[len LE16] 00 00 00 [total LE32] R G B …` — `SendCore.payload(TYPE_CAMERA)`: a whole raw RGB frame in one packet, with no CRC and no 4 KB acks. The constant exists in the app, but this version never calls it, so firmware support is unconfirmed. The web page offers it as "Direct frames (experimental)" for live video.

## Image, GIF and text upload
The payload is split into 4096-byte blocks. Each block gets a 16-byte header, with **all multi-byte fields little-endian** (`ImageAgreement`, `GifAgreement`, `TextAgreement`; the app writes `short2Bytes()[1]` before `[0]`):

```
[len16] [kind] 00 [00 first | 02 continued] [total32] [crc32] [hold16] [slot]
```
- `kind`:
  - `01` = **GIF file bytes**
  - `02` = still image as **raw RGB, row-major** (W×H×3, from `BGRUtils.bitmap2RGB`)
  - `03` = **text** (see below)
- `slot`:
  - `0`–`11` = item saved in the display's memory, which plays in rotation and survives power-off
  - `12` or `13` = "show now"; this keeps running after the phone disconnects
- `hold`: how many seconds a saved item shows (5, 10, 30, 60 or 300). It's `0` for show-now.
- After each 4 KB block, the device notifies `05 00 01 00 st`:
  - `st` 1 = send the next block
  - `st` 3 = done
  - `st` 2 = out of space
  - `st` 0 = invalid command
- To replace the saved list, send `11 00 02 01 0C 00 01 02 … 0B` (clear all). The device replies `05 00 02 01 01`. Then upload the items to slots 0, 1, 2, ….

## Text payload (kind `03`)
The app draws each character into a 1-bit bitmap itself; the display only handles the effects and colours.

```
[nChars LE16] [A] 01 [effect] [speed 0-100] [colourMode] R G B [bgMode] bgR bgG bgB  + per char: [type] [f f f] [bits…]
```
- `A`: `01` for 16×16, 32×32 and 64×64 panels; `00` for 8×32 and 16×64 panels.
- `effect`:
  - `0` pin
  - `1` scroll left
  - `2` scroll right
  - `3` up
  - `4` down
  - `5` flashing
  - `6` breathing
  - `7` snowflakes
  - `8` laser
  - 8×32 panels only support `1` and `2`.
- `colourMode`: `1` = solid colour, `2`–`5` = the four rainbow presets. If R and G are 0, B is forced to at least 1.
- `bgMode`: `0` = none, `1` = colour.
- `f f f` is `FF FF FF` for rendered glyphs. On 8×32 panels it's `00 00 00` (or `FF FF FF` for the built-in font, type 4).
- Glyph types and sizes (bits are row-major, least significant bit = leftmost pixel; `Text1664.getTextData`):

| Type | Cell (w×h) | Bytes | Used for |
|---|---|---|---|
| 1 | 16×12 (12 px drawn, 4 px padding) | 24 | 8×32 panels |
| 2 / 3 | 8×16 / 16×16 | 16 / 32 | 16 px text (half-width / CJK) |
| 5 / 6 | 16×32 / 32×32 | 64 / 128 | 32 px text |
| 7 / 8 | 32×64 / 64×64 | 256 / 512 | 64 px text |

## Panel sizes known to the app
8×32, 16×16, 16×32, 16×48, 16×64, 24×48, 32×32 and 64×64 (`AppData.LED_*`, `CloudGifFragment`).

## Network endpoints the app calls
- `api.e-toys.cn`: crash reports, usage stats, Bluetooth filter list, update check
- `manage.heaton.com.cn/api/rm/`: firmware info and cloud material
- `api.ip138.com`: IP geolocation

None of these are needed to drive the display, so a custom controller can run fully offline.

## Not decoded yet
- Music-rhythm mode (`sendImageRhythm`, `multiplyByteArray`)
- Schedules (`ScheduleAgreement`)
- OTA updates: do not touch these unless you want to risk bricking the panel.
