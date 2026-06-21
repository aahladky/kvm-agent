"""
wol.py — send a Wake-on-LAN magic packet from the desktop orchestrator to the target.

Usage:
    python wol.py AA:BB:CC:DD:EE:FF            # target's NIC MAC
    python wol.py                              # uses env TARGET_MAC
    (optional 2nd arg = broadcast IP, default 255.255.255.255)

Requires on the TARGET: WoL enabled in BIOS, NIC power mgmt "Allow this device to wake the
computer" + "Only allow a magic packet", and Fast Startup OFF (it breaks WoL-from-shutdown).
After the packet, the machine boots, USB re-powers, and the Pico re-joins WiFi (~10-20s).
"""
import socket, sys, os, re, time

mac = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TARGET_MAC", "")).strip()
bcast = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("WOL_BROADCAST", "255.255.255.255")
hexmac = re.sub(r"[^0-9a-fA-F]", "", mac)
if len(hexmac) != 12:
    print("Provide a 12-hex-digit MAC, e.g. python wol.py AA:BB:CC:DD:EE:FF"); sys.exit(1)

packet = bytes.fromhex("ff" * 6 + hexmac * 16)   # 6x 0xFF sync + MAC x16
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
for _ in range(3):
    for port in (9, 7):
        s.sendto(packet, (bcast, port))
    time.sleep(0.2)
s.close()
print(f"sent WoL magic packet to {mac} via {bcast} (ports 9,7)")


def wake_and_wait(mac_addr, pico_ip="192.168.0.183", pico_port=8000, timeout=40):
    """Send WoL, then wait until the Pico (USB-powered by the target) answers — i.e. the
    target is awake and the injector is back. Importable by agent_server as an auto-wake
    preflight. Returns True if the rig came up in time."""
    hx = re.sub(r"[^0-9a-fA-F]", "", mac_addr)
    pkt = bytes.fromhex("ff" * 6 + hx * 16)
    so = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    so.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    so.sendto(pkt, ("255.255.255.255", 9)); so.close()
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            socket.create_connection((pico_ip, pico_port), timeout=3).close()
            return True
        except Exception:
            time.sleep(2)
    return False
