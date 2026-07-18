# pico_fw -- RP2350 (Pico 2 W) port of PiKVM's Pico HID firmware

Replaces the retired CircuitPython firmware (`appliance/pico/stage2_hid.py`) --
that firmware was structurally unsound (no real ACK beyond a hand-rolled envelope,
composite HID collections that die independently on re-enumeration, its own
one-off wire protocol). Rather than keep patching it, this vendors and ports
PiKVM's own Pico HID firmware (`github.com/pikvm/kvmd/tree/master/hid/pico`),
a proven, purpose-built implementation for exactly this Pi<->Pico HID-over-UART
architecture -- CRC16-checked binary protocol, absolute + relative USB mouse,
per-command LED/online status in every response.

**Upstream officially only supports the original Pico (RP2040)** ("Pico 2 is
not supported right now" -- docs.pikvm.org/pico_hid/). This board is a Pico 2 W
(RP2350). The port required, and needed NOTHING beyond:

1. `CMakeLists.txt`: `PICO_BOARD=pico2_w`, `FAMILY=rp2350`, and pico-sdk bumped
   to **2.2.0** (the pinned upstream SDK commit predates RP2350 support
   entirely -- it's 1.5.1-era). pico-sdk 2.2.0 bundles a current TinyUSB with
   native RP2350 device support, so the separate `.tinyusb` checkout upstream's
   Makefile does is no longer needed.
2. `src/ph_com_uart.c`: pins/bus changed from upstream's default (uart1,
   GP20/GP21) to **uart0, GP0(TX)/GP1(RX)** to match this rig's physical wiring
   (`appliance/README.md`; Pi5 GPIO14<->Pico GP1, Pi5 GPIO15<->Pico GP0).
3. `src/ph_com.c`: hardcoded UART transport (`_use_spi = false`) instead of the
   GP22-pull-up/ground jumper upstream uses to pick SPI vs UART at boot -- this
   rig doesn't wire SPI at all, so the extra jumper is unnecessary.
4. `src/ph_cmds.c`: `ph_cmd_kbd_send_key` sends `args[0]` straight to
   `ph_usb_kbd_send_key` as a raw USB HID usage code, skipping
   `ph_usb_keymap()` (PiKVM's own internal keycode-ID indirection). The host
   bridge (`appliance/pi5/pikvm_proto.py`) already owns a name->USB-HID-usage
   table ported from the old firmware's `adafruit_hid.keycode.Keycode` names,
   so keeping PiKVM's separate ID scheme would just be a second table to
   maintain in lockstep for no benefit.

No RP2350-specific source changes were needed anywhere else -- the firmware
uses only pico-sdk HAL calls (`hardware/uart.h`, `hardware/gpio.h`,
`hardware/pio.h` via the vendored `ps2x2pico` PIO code), which pico-sdk 2.x
already abstracts identically across RP2040 and RP2350.

**No SPI, no PS/2 output, no bridge mode are wired on this rig** -- the
composited descriptor still includes that code (unmodified, harmless), but
only the USB keyboard + absolute USB mouse interfaces are ever active, per the
pin-default logic in `ph_outputs.c` (all mode-select GPIOs float pulled-up ->
USB keyboard + absolute USB mouse, no jumpers required beyond the UART wiring).

Enumerates as **VID:PID `1209:eda2`** ("PiKVM HID", pid.codes org allocation)
-- NOT the old Adafruit `239a:8162`. Any udev rule or libvirt `<hostdev>` VID:PID
match must target the new ID (see the host's `/etc/udev/rules.d/99-pico-hid-
passthrough.rules` and [[pico_passthrough_mouse_dead]] memory).

## Build

```
make            # clones pico-sdk 2.2.0 + ps2x2pico on first run, builds hid.uf2
```

Needs `gcc-arm-none-eabi`, `cmake`, `build-essential` on the host.

## Flash

This firmware has **no CDC serial console** (unlike the retired CircuitPython
firmware), so there's no software bootloader-reset trick -- put the board in
UF2 bootloader mode by **holding BOOTSEL while power-cycling/replugging USB**,
then:

```
cp hid.uf2 /run/media/$USER/RP2350/     # mountpoint name may vary
```

It auto-reboots into the new firmware once the copy completes.

## Wire protocol

See `appliance/pi5/pikvm_proto.py`'s module docstring for the full frame format,
CRC16 spec, command set, and the absolute-mouse coordinate quirk
(`x_usb = (x_proto + 32768) / 2`).
