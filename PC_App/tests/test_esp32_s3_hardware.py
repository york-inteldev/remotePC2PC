import ctypes
import os
import queue
import sys
import time
import unittest
from ctypes import wintypes

import protocol
from ble_transport import BleGattClient


_ADDRESS = os.environ.get("REMOTE_KVM_S3_BLE_ADDRESS", "")
_VK_F24 = 0x87
_USAGE_F24 = 0x73
_WM_KEYDOWN = 0x100
_WM_KEYUP = 0x101
_LLKHF_INJECTED = 0x10


class _KeyboardData(ctypes.Structure):
    _fields_ = [
        ("vk", wintypes.DWORD),
        ("scan", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("extra", ctypes.c_size_t),
    ]


class _KeyboardHook:
    def __init__(self):
        self.events = []
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
        )
        self.callback = callback_type(self._on_keyboard)
        self.user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int,
            callback_type,
            wintypes.HINSTANCE,
            wintypes.DWORD,
        ]
        self.user32.SetWindowsHookExW.restype = wintypes.HANDLE
        self.user32.CallNextHookEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self.user32.CallNextHookEx.restype = ctypes.c_ssize_t
        self.user32.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
        self.user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        self.user32.PeekMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self.user32.PeekMessageW.restype = wintypes.BOOL
        self.user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        self.user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        self.handle = self.user32.SetWindowsHookExW(13, self.callback, None, 0)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def _on_keyboard(self, code, message, pointer):
        if code >= 0:
            data = ctypes.cast(pointer, ctypes.POINTER(_KeyboardData)).contents
            if data.vk == _VK_F24:
                self.events.append((message, data.flags, time.monotonic()))
                return 1
        return self.user32.CallNextHookEx(None, code, message, pointer)

    def has(self, message):
        return any(
            event == message and not flags & _LLKHF_INJECTED
            for event, flags, _ in self.events
        )

    def pump(self, link, timeout, condition=None, allow_disconnect=False):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = wintypes.MSG()
            while self.user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                self.user32.TranslateMessage(ctypes.byref(message))
                self.user32.DispatchMessageW(ctypes.byref(message))
            while True:
                try:
                    event = link.events.get_nowait()
                except queue.Empty:
                    break
                if "error" in event[0] or (
                    event[0] == "disconnected" and not allow_disconnect
                ):
                    raise AssertionError(f"BLE transport event: {event}")
            if condition is not None and condition():
                return True
            time.sleep(0.003)
        return condition is not None and bool(condition())

    def close(self):
        if not self.user32.UnhookWindowsHookEx(self.handle):
            raise ctypes.WinError(ctypes.get_last_error())


@unittest.skipUnless(
    sys.platform == "win32" and _ADDRESS,
    "Set REMOTE_KVM_S3_BLE_ADDRESS on Windows to run the ESP32-S3 hardware test",
)
class Esp32S3HardwareTests(unittest.TestCase):
    def _connect(self, link):
        for _ in range(3):
            link.scan()
            event, value = link.events.get(timeout=15)
            self.assertEqual(event, "scan_complete", (event, value))
            device = next(
                (item for item in value if item.address.casefold() == _ADDRESS.casefold()),
                None,
            )
            if device is not None:
                link.connect(device)
                event, value = link.events.get(timeout=20)
                self.assertEqual(event, "connected", (event, value))
                return
        self.fail(f"ESP32-S3 {_ADDRESS} was not found in three BLE scans")

    def test_watchdog_blocks_input_until_valid_heartbeat_and_releases_on_disconnect(self):
        link = BleGattClient()
        hook = None
        try:
            self._connect(link)
            hook = _KeyboardHook()
            hook.pump(link, 0.5)
            hook.events.clear()

            self.assertTrue(link.send(protocol.heartbeat_packet()))
            self.assertTrue(link.send(protocol.key_packet(_USAGE_F24, True)))
            self.assertTrue(hook.pump(link, 4, lambda: hook.has(_WM_KEYDOWN)))
            for _ in range(5):
                hook.pump(link, 0.4)
                self.assertFalse(hook.has(_WM_KEYUP), hook.events)
                self.assertTrue(link.send(protocol.heartbeat_packet()))
            last_heartbeat_time = time.monotonic()
            self.assertTrue(hook.pump(link, 4, lambda: hook.has(_WM_KEYUP)))
            up_time = next(
                timestamp for message, flags, timestamp in hook.events
                if message == _WM_KEYUP and not flags & _LLKHF_INJECTED
            )
            self.assertGreaterEqual(up_time - last_heartbeat_time, 1.2)
            self.assertLessEqual(up_time - last_heartbeat_time, 3.5)

            hook.events.clear()
            bad_checksum = bytearray(protocol.heartbeat_packet())
            bad_checksum[-1] ^= 0x01
            self.assertTrue(link.send(bytes(bad_checksum)))
            self.assertTrue(link.send(protocol.key_packet(_USAGE_F24, True)))
            hook.pump(link, 0.7)
            self.assertFalse(hook.has(_WM_KEYDOWN), hook.events)

            self.assertTrue(
                link.send(protocol.encode_frame(protocol.MSG_HEARTBEAT, b"\x00"))
            )
            self.assertTrue(link.send(protocol.key_packet(_USAGE_F24, True)))
            hook.pump(link, 0.7)
            self.assertFalse(hook.has(_WM_KEYDOWN), hook.events)

            self.assertTrue(link.send(protocol.heartbeat_packet()))
            self.assertTrue(link.send(protocol.key_packet(_USAGE_F24, True)))
            self.assertTrue(hook.pump(link, 4, lambda: hook.has(_WM_KEYDOWN)))
            self.assertTrue(link.send(protocol.release_all_packet()))
            self.assertTrue(hook.pump(link, 4, lambda: hook.has(_WM_KEYUP)))

            hook.events.clear()
            self.assertTrue(link.send(protocol.heartbeat_packet()))
            self.assertTrue(link.send(protocol.key_packet(_USAGE_F24, True)))
            self.assertTrue(hook.pump(link, 4, lambda: hook.has(_WM_KEYDOWN)))
            link.disconnect()
            self.assertTrue(
                hook.pump(
                    link, 4, lambda: hook.has(_WM_KEYUP), allow_disconnect=True
                )
            )
        finally:
            if hook is not None:
                link.send(protocol.release_all_packet())
                try:
                    link.close()
                    hook.pump(link, 0.3, allow_disconnect=True)
                finally:
                    hook.close()
            else:
                link.close()


if __name__ == "__main__":
    unittest.main()
