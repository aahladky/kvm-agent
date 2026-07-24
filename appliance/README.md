# Pi 5 + Pico 2 W HID appliance

The appliance is the physical action channel for `kvm-agent`:

```text
host ApplianceClient → HTTP → Pi 5 hid_bridge.py
                            → CRC16 UART → Pico 2 W firmware
                                         → USB keyboard/mouse → target
```

Capture remains on the host. The Pi provides a persistent UART owner and HTTP API; the
Pico presents a composite USB keyboard and absolute mouse to the target.

## Current components

| Path | Purpose |
|---|---|
| `pi5/hid_bridge.py` | Threaded HTTP service; validates requests, serializes access to the Pico, returns decoded wire ACKs, and records daemon evidence. |
| `pi5/pikvm_proto.py` | Binary PiKVM Pico-HID framing, CRC, sequence/retry logic, key map, and response decoding. |
| `pi5/hid-bridge.service` | Always-on systemd unit for the deployed bridge. |
| `pico_fw/` | RP2350/Pico 2 W port of PiKVM’s TinyUSB HID firmware. |
| `kvm_agent/hardware/appliance.py` | Host-side client. It raises on failed delivery and retains every bridge response for the owning run. |

The retired CircuitPython/ASCII implementation is under `_archive/` and is not
compatible with the current bridge.

## Wiring

Both sides are 3.3 V. Connect signal and ground only; do not join power rails.

| Signal | Pi 5 header | Pico 2 W |
|---|---|---|
| Pi TX → Pico RX | GPIO14/TXD, pin 8 | GP1, pin 2 |
| Pi RX ← Pico TX | GPIO15/RXD, pin 10 | GP0, pin 1 |
| Ground | pin 6 | pin 3 |

The deployed serial device is `/dev/ttyAMA0`. `/dev/serial0` points at a different UART
on this Pi and silently sends bytes to the wrong interface.

## HTTP API

- `GET /health`
- `GET /hid/probe`
- `POST /hid/move?x=&y=`
- `POST /hid/click`, `/rclick`, `/down`, `/up`, `/home`, `/clear`
- `POST /hid/key?name=`
- `POST /hid/type?text=`
- `POST /hid/combo?spec=`
- `POST /hid/scroll?ticks=`
- `POST /hid/set_screen?w=&h=`

Every HID response includes the Pico’s decoded wire result where applicable. Online
flags are diagnostic only; the host’s camera-verified HID check is the delivery gate.

## Deploy the Pi bridge

The service runs `/home/aaron/hid_bridge.py` and imports
`/home/aaron/pikvm_proto.py`. Deploy both source files and the unit, then restart:

```bash
scp appliance/pi5/hid_bridge.py appliance/pi5/pikvm_proto.py aaron@192.168.0.29:/home/aaron/
scp appliance/pi5/hid-bridge.service aaron@192.168.0.29:/tmp/
ssh aaron@192.168.0.29 \
  'sudo cp /tmp/hid-bridge.service /etc/systemd/system/hid-bridge.service &&
   sudo systemctl daemon-reload &&
   sudo systemctl restart hid-bridge &&
   systemctl --no-pager --full status hid-bridge'
```

Each daemon start creates `/home/aaron/runs/hid_bridge_<timestamp>/commands.jsonl`.
The host also records exact responses inside each agent run, so the appliance log is a
crash/transport diagnostic rather than the only evidence copy.

Verify from the host:

```bash
curl http://192.168.0.29:8080/health
curl -X POST http://192.168.0.29:8080/hid/clear
```

Then use `agent_loop_holo.boot()` for the authoritative camera-verified keyboard and
mouse round trip.

## Firmware

From `appliance/pico_fw/`:

```bash
make
```

Dependencies live in visible `pico_fw/deps/`; build output and `hid.uf2` go to
`runs/pico_fw_build_<timestamp>/`. See `pico_fw/README.md` for port-specific details
and flashing.
