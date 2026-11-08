# LED panel BLE protocols

This repo drives two unrelated families of Bluetooth LED displays. The web controller detects which one you connect to from the Bluetooth service it exposes.

| Family | App | Service | Panels |
|---|---|---|---|
| **iDotMatrix** | iDotMatrix 2.1.6 (`com.tech.idotmatrix`) | `000000fa-…` | 16×16 to 64×64 full-colour pixel boards |
| **XiaoBai / "small white"** | XiaoBai LED 1.3.51 (`com.smallwhite.led`) | `0000ae00-<model>-…` | 10×30 to 32×32 badges, signs and caps |

Both were decompiled with jadx 1.5.1. Paths below are relative to jadx's `sources/` folder.

---

# Part 1: iDotMatrix

### Transport
- **Service** `000000fa-0000-1000-8000-00805f9b34fb`
- **Write** `0000fa02-…` (write-without-response). Notifications come back on a notify characteristic in the same service.
- The app requests MTU 512. It sends 509-byte writes when the MTU is high and 18-byte writes otherwise (`GifAgreement.getSendData`).
- **Discovery:** the advertised manufacturer data contains `54 52 00 70` ("TR\0p"). `BleConfig.matchProduct` filters on it. Inside that block, byte 5 is the panel shape and byte 10 is the PID.
- **No encryption on normal commands.** The native `libAES.so` (`csh/tiro/cc/aes`) is only used for the "find my device" `LOCATE` ping (`BleProtocol.locationDevice`).

### Short commands
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

### Graffiti pixels (type `05 01`)
`[len LE16] 05 01 moveType R G B x1 y1 x2 y2 …`. One colour per packet, any number of points (`SendCore.sendDiyImageData`).

### Direct frame (type `00 00`, untested)
`[len LE16] 00 00 00 [total LE32] R G B …` — `SendCore.payload(TYPE_CAMERA)`: a whole raw RGB frame in one packet, with no CRC and no 4 KB acks. The constant exists in the app, but this version never calls it, so firmware support is unconfirmed. The web page offers it as "Direct frames (experimental)" for live video.

### Image, GIF and text upload
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

### Text payload (kind `03`)
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

### Panel sizes known to the app
8×32, 16×16, 16×32, 16×48, 16×64, 24×48, 32×32 and 64×64 (`AppData.LED_*`, `CloudGifFragment`).

### Network endpoints the app calls
- `api.e-toys.cn`: crash reports, usage stats, Bluetooth filter list, update check
- `manage.heaton.com.cn/api/rm/`: firmware info and cloud material
- `api.ip138.com`: IP geolocation

None of these are needed to drive the display, so a custom controller can run fully offline.

### Not decoded yet
- Music-rhythm mode (`sendImageRhythm`, `multiplyByteArray`)
- Schedules (`ScheduleAgreement`)
- OTA updates: do not touch these unless you want to risk bricking the panel.


---

# Part 2: XiaoBai LED (`com.smallwhite.led` 1.3.51)

### Transport
- Each model has its **own 128-bit service UUID**: `0000ae00-<model>-…`. The write characteristic is the same UUID with `ae00` → `ae01`. A few models also have a notify characteristic, `ae02`. (`remote/util/BleClient.java`)
- The device advertises its service UUID, and the app identifies the model from it. There is no name filter (`ui/ScanBleActivity.java`).
- Writes are **write-without-response** (`setWriteType(1)`). The app splits each packet into **220-byte pieces (488 on 32×32)** and waits **50 ms** between pieces (`BleClient.sendOrder/sendOrder3`, `TIME = 50`).
- No encryption and no checksum. The byte before the closing `88` is `00`, except in animation frames (see below).

### Models (`BleClient.onServicesDiscovered`)

| Service UUID | Model | Panel (w×h) | Pixel format | Notes |
|---|---|---|---|---|
| `0000ae00-0000-0000-8000-00805f9b34fb` | 1 | 30×10 | RGB555 | |
| `0000ae00-1030-0001-8000-00805f9b34fb` | 4 | 30×10 | RGB555 | music |
| `0000ae00-1232-0000-8000-00805f9b34fb` | 3 | 32×12 | RGB444 | |
| `0000ae00-1232-0001-8000-00805f9b34fb` | 6 | 32×12 | RGB444 | alarm clock |
| `0000ae00-1232-0002-8000-00805f9b34fb` | 8 | 32×12 | RGB444 | sound |
| `0000ae00-1232-0003-8000-00805f9b34fb` | 9 | 32×12 | RGB444 | sound |
| `0000ae00-1232-0004-8010-00805f9b34fb` | 16 | 32×12 | RGB444 | |
| `0000ae00-1229-1111-8012-00805f9b36fb` | 19 | 29×12 | RGB444 | uses the 12×32 sender |
| `0000ae00-1224-0100-8000-00805f9b34fb` | 7 | 24×12 | 1-bit | |
| `0000ae00-1248-0000-8000-00805f9b34fb` | 5 | 48×12 | 1-bit | |
| `0000ae00-1248-0100-8001-00805f9b34fb` | 10 | 48×12 | 1-bit | |
| `0000ae00-1248-0100-8002-00805f9b34fb` | 11 | 48×12 | 1-bit | |
| `0000ae00-1248-0100-800e-00805f9b34fb` | 14 | 48×12 | 1-bit | |
| `0000ae00-1248-0100-800f-00805f9b35fb` | 15 | 48×12 | 1-bit | |
| `0000ae00-1248-0100-8011-00805f9b35fb` | 17 | 48×12 | 1-bit | hat |
| `0000ae00-1248-0100-8013-00805f9b36fb` | 18 | 48×12 | 1-bit | tissue box, text-oriented |
| `0000ae00-1248-1111-8014-00805f9b36fb` | 21 | 48×12 | 1-bit | |
| `0000ae00-1616-0001-800c-00805f9b34fb` | 12 | 16×16 | RGB444 | |
| `0000ae00-1616-0002-800c-00805f9b34fb` | 13 | 16×16 | RGB444 | notify `ae02` |
| `0000ae00-1616-1111-8013-00805f9b36fb` | 20 | 16×16 | RGB444 | "K3": text frames use command `BA` |
| `0000ae00-1616-1111-8015-00805f9b36fb` | 22 | 16×16 | RGB444 | notify `ae02` |
| `0000ae00-1616-1111-8016-00805f9b36fb` | 23 | 16×16 | RGB444 | "T2", notify `ae02` |
| `0000ae00-3232-0000-8000-00805f9b34fb` | 2 | 32×32 | RGB444, split halves | 488-byte writes |

**Added in app 1.4.02** (packet building moved into the native `libstrongled.so`, called through `com.xiaobai.led.lib.AppJni`):

| Service UUID | Model | Panel | Pixels | Notes |
|---|---|---|---|---|
| `0000ae00-1111-0002-8000-00805f9b36fb` | 41 | 11×11 | RGB444 | "LED_11X11"; the app's "跑马灯音箱" (marquee speaker) group 35/38/41/44, e.g. the **HBS613 LED** speaker. 488-byte writes. Also exposes `0xFF12` (speaker audio, not used for the display). |

#### 11×11 speakers (model 41)
Same `55 … 88` commands, but every `B1` packet carries exactly one 11×11 frame (242 bytes, column-major RGB444) and byte 3 is the packet/frame number, not a byte offset. The app waits 100 ms between packets.

| What | Packets | Native function |
|---|---|---|
| Still image | `55 B1 00 00 <242> 00 88`, then `55 B6 01 00 00 00 00 88` | `getImageData11x11` / `getMaterialImageData` |
| Save to slot *n* (optional) | `55 C6 <n+1>` (3 bytes, no `88`), after the above | same |
| Animation | `55 B1 00 <frame#> <242> <speed> 88` per frame, then `55 B6 02 00 00 00 00 88`. `speed` = the material file's per-frame byte − 2 (5 if that is < 1) | `getGifData11x11` |
| Scrolling text | strip encoded column-major, zero-padded to whole 11-column frames; `55 B1 00 <i> <242> 00 88` per frame, then `55 B6 03 <dir> <speed> <columns LE16> 88`. Direction 0 (still) sends the first frame as a still image instead. The app defaults to dir 2 (left), speed 5. | `getScrollData` (step 1; 16×16 uses step 2, 12×32 step 3) |
| Brightness | `55 B8 <0-5> 05 00 00 88` | unchanged |
| Set clock | `55 C1 10 <ss> <mm> <hh> 00 88`, all three **BCD** (24 h); the app sends it 1 s after every connection. `55 C1 11 00 88` switches to the built-in clock. | `v2/v` message 101 |
| Music bars | first upload a colour "background" frame (`55 B1 00 00 <242> 00 88`, **no** `B6` show); then repeat `55 B7 <11 heights 0-11> <style> 88` about every 100 ms. The speaker lights each column up to its height using the background's colours. The app's backgrounds are `music_back_11x11_1..4` (vertical rainbow, cyan, white, horizontal rainbow). | `q.C` / `q.D` / `q.o` (7 bands interpolated to 11) |

### Framing
Every command is `55 <cmd> … 88`.

| Command | Bytes | Meaning | Source |
|---|---|---|---|
| Frame data | `55 B1 <offset LE16> <chunk…> <f> 88` | write pixel data at a byte offset; `f` = `00`, or the frame speed in animations | `GiFUtil.sendIMG*/sendData*/sendGIF*` |
| Show image | `55 B6 01 00 00 00 00 88` | display the uploaded data as a still image | `DataDirection.Image32x32Display` |
| Show animation | `55 B6 02 00 00 00 00 88` | play the uploaded frames | `DataDirection.GIF32x32Display` |
| Show text | `55 B6 03 <dir> <speed> <width LE16> 88` | scroll the uploaded strip | `DataDirection.TextWidthDisplay` |
| Brightness | `55 B8 <0-5> 05 00 00 88` | the app's slider is 0–5, default 3 | `CoolLEDMainActivity.sbLightListener` |
| Built-in animation | `55 B4 <n> 88` (`55 B4 <n> 05 00 00 88` on 10×30) | play a stored animation | `MaterialGifFragment` |
| Music bars | `55 B7 <7 heights> <hollow 0/1> 88` | live equaliser; heights 0–11 on 12-row panels (32×32: `h·22/30−1`) | `MusicBleUtil` |
| Music mode | `55 B5 <hollow> <style> 88` | | `MusicFragment` |
| Music pause | `55 B9 88` | | `DataDirection.gitMusic12x32Pause` |
| Set clock | `55 C1 10 <ss> <mm BCD> <hh BCD> 00 88` | alarm models (on model 13: `55 C1 0F ss mm hh DD MM YY 00 88`, binary) | `AlarmBleUtil` |
| Show clock | `55 C1 11 00 88` | | `AlarmBleUtil.showAlarm` |
| Set alarm | `55 C2 10 <ss> <mm BCD> <hh BCD> 00 88` | | `AlarmBleUtil.sendAlarm` |
| Power-on animation | `55 C3 <0/1>` | | `SettingActivity` |
| Factory reset | `55 C5 00` | | `SettingActivity` |
| Firmware version | `BD` | | `DataDirection.Version` |

`dir`:
- `0` still (oscillate on some 12×32 models)
- `1` scroll right
- `2` scroll left (the default)
- `3` up
- `4` down

`speed` is 1–10, and **lower is faster**: the app sends `10 − slider`, minimum 1, default 5.

### Pixel formats (`DiyBleUtil.SendIMG*`)
- **1-bit (12×24, 12×48):** row by row, left to right, 8 pixels per byte, **least significant bit = leftmost pixel**. One frame is `w·h/8` bytes (72 on 12×48, 36 on 12×24).
- **RGB444 (12×32, 12×29, 16×16):** **column by column** (x outer, y inner), 2 bytes per pixel: `[(G>>4)<<4 | B>>4] [R>>4]`.
- **RGB444 split (32×32):** the same 2-byte pixels, but first all columns of rows 0–15, then all columns of rows 16–31.
- **RGB555 (10×30):** column by column, 2 bytes per pixel: `[(g5&7)<<5 | b5] [r5<<2 | g5>>3]`. Images use their own framing: `55 B1 01 01 L L <data> L L 00 88`, where `L L` = `(len%256)+6, (len+6)/256`, followed by `55 B6 00 00 0A 1D 88`.

Chunk size per `B1` packet:
- one frame on 1-bit panels
- 768 bytes on 12×32 and 12×29
- 512 bytes on 16×16 and 32×32 (four packets per 32×32 frame)

The offset field is the **byte offset, little-endian** (`DataDirection.TOHEX`; the image senders write `00, i·3` and `00, i·2`, which is the same thing).

### Text
The app draws the text into a strip as tall as the panel, then uploads it with `B1` packets and finishes with `B6 03`.
- **1-bit panels:** the strip is padded to whole panel widths and cut into frames of `w` columns, each packed as above. Width field = text columns `+ 6` on 12×48, or padded columns on 12×24.
- **12×32 and 16×16:** a continuous column-by-column RGB444 stream padded to the chunk size. On 12×32, when `dir ≠ 0` the app adds 8 blank columns first. Width field = columns.
- **32×32:** split halves; width field = `bytes / 32`. This one is best-effort.
- **10×30:** uses its own 600-byte framing (`TextSendUtil.send10X30Data`). Not implemented.

### Animation
Frames are concatenated. Each frame goes in its own `B1` packet with offset `i × frameBytes`, and its last byte (before `88`) is the frame speed (`speed × 5`, where the app's default speed is 5). Then send `55 B6 02 00 00 00 00 88`. On 32×32 one frame spans four packets, so the controller doesn't offer animation there.

### Network
- `api.xiaobaizhuli.com` and `app.xiaobaizhuli.com` (the policy pages are plain HTTP): account login and device "pay" status.
- The payment flag only unlocks the cloud **AI voice assistant** (iFlytek); the LED commands don't depend on it.

### Not implemented in the controller
- 10×30 text framing
- built-in animation indexes
- alarms
- the K3's `BA` text variant
- classic-Bluetooth audio pairing on the notify-capable 16×16 models
