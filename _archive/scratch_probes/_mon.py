"""
_mon.py — WH_MOUSE_LL low-level mouse hook. Logs EVERY mouse event Windows
receives (incl. injected HID from the Pico) to _mon.log: timestamp, message,
x, y, and whether it was INJECTED. This reads the OS-level result of the Pico's
HID reports directly — no screenshot / cursor-visibility ambiguity.

Usage: python _mon.py [seconds]
"""
import ctypes
import ctypes.wintypes as w
import sys, time, threading

DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0

WH_MOUSE_LL = 14
WM_QUIT = 0x0012
MSGS = {
    0x0200: "MOVE", 0x0201: "L_DOWN", 0x0202: "L_UP",
    0x0204: "R_DOWN", 0x0205: "R_UP", 0x0207: "M_DOWN", 0x0208: "M_UP",
    0x020A: "WHEEL",
}
LLMHF_INJECTED = 0x01

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", w.POINT), ("mouseData", w.DWORD),
                ("flags", w.DWORD), ("time", w.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

HOOKPROC = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_int, w.WPARAM, w.LPARAM)

# 64-bit safe prototypes (without these, pointers truncate to 32-bit -> hook fails)
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, w.DWORD]
user32.CallNextHookEx.restype = w.LPARAM
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, w.WPARAM, w.LPARAM]
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.GetMessageW.argtypes = [ctypes.c_void_p, w.HWND, ctypes.c_uint, ctypes.c_uint]
user32.PostThreadMessageW.argtypes = [w.DWORD, ctypes.c_uint, w.WPARAM, w.LPARAM]
kernel32.GetModuleHandleW.restype = ctypes.c_void_p
kernel32.GetModuleHandleW.argtypes = [w.LPCWSTR]

logf = open("_mon.log", "w", buffering=1)
t0 = time.time()

def proc(nCode, wParam, lParam):
    if nCode == 0:
        ms = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
        name = MSGS.get(wParam, hex(wParam))
        inj = "INJ" if (ms.flags & LLMHF_INJECTED) else "phys"
        logf.write(f"{time.time()-t0:6.2f}  {name:7} ({ms.pt.x:5},{ms.pt.y:5})  {inj}\n")
    return user32.CallNextHookEx(0, nCode, wParam, lParam)

cb = HOOKPROC(proc)
hook = user32.SetWindowsHookExW(WH_MOUSE_LL, cb, kernel32.GetModuleHandleW(None), 0)
if not hook:
    logf.write("FAILED to set hook: %d\n" % ctypes.get_last_error()); sys.exit(1)
logf.write("hook set; listening %.0fs\n" % DURATION)

main_tid = kernel32.GetCurrentThreadId()
def killer():
    time.sleep(DURATION)
    user32.PostThreadMessageW(main_tid, WM_QUIT, 0, 0)
threading.Thread(target=killer, daemon=True).start()

msg = w.MSG()
while user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) != 0:
    user32.TranslateMessage(ctypes.byref(msg))
    user32.DispatchMessageW(ctypes.byref(msg))

user32.UnhookWindowsHookEx(hook)
logf.write("done\n")
logf.close()
