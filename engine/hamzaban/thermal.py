"""How hot the Mac is, as macOS itself reports it (NSProcessInfo.thermalState).

0 nominal, 1 fair, 2 serious (macOS is already slowing the chip down to cool
it), 3 critical. Read through the Objective-C runtime, so no extra package is
needed; anywhere that fails it reports "nominal" and nothing changes.
"""
import ctypes
import ctypes.util
import threading
import time

NOMINAL, FAIR, SERIOUS, CRITICAL = range(4)
CHECK_EVERY = 5.0


def _reader():
    try:
        objc = ctypes.CDLL(ctypes.util.find_library("objc"))
        ctypes.CDLL(ctypes.util.find_library("Foundation"))
        objc.objc_getClass.restype = objc.sel_registerName.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = objc.sel_registerName.argtypes = [ctypes.c_char_p]
        send = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
        get_obj = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(send)
        get_long = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)(send)
        info = get_obj(objc.objc_getClass(b"NSProcessInfo"), objc.sel_registerName(b"processInfo"))
        sel = objc.sel_registerName(b"thermalState")
        if not info:
            return None
        return lambda: int(get_long(info, sel))
    except (OSError, AttributeError, TypeError):
        return None


_read = _reader()
_lock = threading.Lock()
_last = [-CHECK_EVERY, NOMINAL]  # [when checked, level]


def level() -> int:
    with _lock:
        now = time.monotonic()
        if _read and now - _last[0] >= CHECK_EVERY:
            try:
                _last[1] = min(CRITICAL, max(NOMINAL, _read()))
            except Exception:
                _last[1] = NOMINAL
            _last[0] = now
        return _last[1]


def hot() -> bool:
    return level() >= SERIOUS
