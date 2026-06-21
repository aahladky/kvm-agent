"""
pico_diag_windows.py — run on the WINDOWS host the Pico is plugged into.

Classifies why the Pico "slept": USB power-down (Windows suspended the port) vs a WiFi drop.
Gathers: COM ports, CIRCUITPY boot_out.txt, the USB-selective-suspend setting, and recent
System-event-log USB/power events. (pip install pyserial for the COM list.)
    python pico_diag_windows.py
"""
import subprocess, os

def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=40, shell=True).stdout
    except Exception as e:
        return f"<err {e}>"

print("=== COM ports (look for vid 2e8a/239a = the Pico) ===")
try:
    from serial.tools import list_ports
    for p in list_ports.comports():
        print(f"  {p.device}  vid={p.vid and hex(p.vid)}  {p.description}  {p.manufacturer}")
except Exception:
    print(run('powershell -NoProfile "Get-PnpDevice -Class Ports | '
              'Format-Table -Auto FriendlyName,Status"'))

print("\n=== CIRCUITPY boot_out.txt (last boot) ===")
hit = False
for d in "DEFGHIJKL":
    p = d + ":\\boot_out.txt"
    if os.path.exists(p):
        hit = True; print(f"  [{d}:]\n" + open(p).read())
if not hit:
    print("  (no CIRCUITPY drive mounted right now)")

print("=== USB selective suspend, AC (Index 0x1 = ENABLED = can power down the Pico) ===")
print(run('powercfg /q SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 '
          '48e6b7a6-50f5-4782-a5d4-53bb8f07e226'))

print("=== recent System-log USB / power events (last 6h) ===")
print(run('powershell -NoProfile "Get-WinEvent -FilterHashtable @{LogName=\'System\';'
          'StartTime=(Get-Date).AddHours(-6)} -ErrorAction SilentlyContinue | '
          'Where-Object {$_.ProviderName -match \'USB|Kernel-Power|Kernel-PnP\'} | '
          'Select-Object TimeCreated,Id,ProviderName,Message | Format-List | '
          'Out-String -Width 200"'))
