from __future__ import annotations

import ctypes
import math
import queue
import sys
import threading
import tkinter as tk
from ctypes import wintypes
from tkinter import messagebox, ttk

try:
    import cv2
    import serial
    from PIL import Image, ImageTk
    from serial.tools import list_ports
    from ble_transport import BLE_DEVICE_NAME, BleGattClient
except ImportError as exc:
    raise SystemExit(
        "Required packages are missing. From the repository root, run: "
        "py -m pip install -r .\\PC_App\\requirements.txt"
    ) from exc

import protocol
from keymap import keysym_to_usage

WH_MOUSE_LL = 14
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
WM_XBUTTONDBLCLK = 0x020D


class _MouseHookData(ctypes.Structure):
    _fields_ = [
        ("point", wintypes.POINT),
        ("mouse_data", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("extra_info", ctypes.c_size_t),
    ]


_MOUSE_HOOK_PROC = None
_USER32 = None
_KERNEL32 = None
if sys.platform == "win32":
    _MOUSE_HOOK_PROC = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t
    )
    _USER32 = ctypes.WinDLL("user32", use_last_error=True)
    _USER32.SetWindowsHookExW.argtypes = [
        ctypes.c_int,
        _MOUSE_HOOK_PROC,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _USER32.SetWindowsHookExW.restype = ctypes.c_void_p
    _USER32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
    _USER32.UnhookWindowsHookEx.restype = wintypes.BOOL
    _USER32.CallNextHookEx.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_size_t,
        ctypes.c_ssize_t,
    ]
    _USER32.CallNextHookEx.restype = ctypes.c_ssize_t
    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _KERNEL32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _KERNEL32.GetModuleHandleW.restype = ctypes.c_void_p


class CameraCapture:
    def __init__(self, device_index: int) -> None:
        self.device_index = device_index
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._frame = None
        self._error: str | None = None
        self._thread = threading.Thread(target=self._capture, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _capture(self) -> None:
        capture = None
        try:
            capture = cv2.VideoCapture(self.device_index, cv2.CAP_DSHOW)
            if not capture.isOpened():
                self._error = (
                    f"Camera index {self.device_index} did not open. "
                    "Try another index and check Windows camera privacy settings."
                )
                return

            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            while not self._stop.is_set():
                ok, frame = capture.read()
                if ok and frame is not None:
                    with self._lock:
                        self._frame = frame
                elif self._stop.wait(0.05):
                    break
                else:
                    self._error = "The capture device stopped returning frames."
        except cv2.error as exc:
            self._error = f"Camera error: {exc}"
        finally:
            if capture is not None:
                capture.release()

    def latest_frame(self):
        with self._lock:
            return None if self._frame is None else self._frame.copy()

    @property
    def error(self) -> str | None:
        return self._error

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)


class RemoteKvmApp:
    BAUD_RATE = 115200
    HEARTBEAT_MS = 500
    CAMERA_POLL_MS = 33

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("PC B Remote KVM")
        self.root.geometry("1100x760")
        self.root.minsize(700, 480)

        self.camera: CameraCapture | None = None
        self.serial_port = None
        self.serial_ready = False
        self.ble_link = BleGattClient()
        self.ble_ready = False
        self.ble_connecting = False
        self.ble_scanning = False
        self.active_transport: str | None = None
        self.ble_devices = {}
        self.closing = False
        self._mouse_hook_handle = None
        self._mouse_hook_proc = None
        self.forward_input = tk.BooleanVar(value=False)
        self.camera_index = tk.StringVar(value="0")
        self.transport_mode = tk.StringVar(value="BLE GATT (ESP32-S3)")
        self.port_name = tk.StringVar()
        self.ble_device_name = tk.StringVar()
        self.target_width = tk.StringVar(value="1920")
        self.target_height = tk.StringVar(value="1080")
        self.status = tk.StringVar(value="Open the USB capture device to begin.")

        self._photo = None
        self._last_frame = None
        self._image_rect: tuple[float, float, float, float] | None = None
        self._inside_video = False
        self._last_normalized: tuple[float, float] | None = None
        self._fractional_dx = 0.0
        self._fractional_dy = 0.0
        self._wheel_remainder = 0.0

        self._build_ui()
        self._on_transport_changed()
        self.root.bind("<FocusOut>", self._on_focus_out, add="+")
        self.root.bind("<Unmap>", self._on_window_unmap, add="+")
        self.root.bind_all("<KeyPress>", self._on_key_press, add="+")
        self.root.bind_all("<KeyRelease>", self._on_key_release, add="+")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(self.CAMERA_POLL_MS, self._poll_camera)
        self.root.after(self.HEARTBEAT_MS, self._send_heartbeat)
        self.root.after(50, self._poll_ble_events)

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        controls = ttk.Frame(self.root, padding=(8, 8, 8, 4))
        controls.grid(row=0, column=0, sticky="ew")

        ttk.Label(controls, text="Capture index:").grid(row=0, column=0, padx=(0, 4))
        ttk.Spinbox(
            controls, from_=0, to=15, width=4, textvariable=self.camera_index
        ).grid(row=0, column=1, padx=(0, 4))
        ttk.Button(controls, text="Open camera", command=self._open_camera).grid(
            row=0, column=2, padx=(0, 14)
        )

        ttk.Label(controls, text="Transport:").grid(
            row=0, column=3, padx=(0, 4)
        )
        self.transport_box = ttk.Combobox(
            controls,
            width=22,
            textvariable=self.transport_mode,
            values=("USB CDC (Pico)", "BLE GATT (ESP32-S3)"),
            state="readonly",
        )
        self.transport_box.grid(row=0, column=4, sticky="w")
        self.transport_box.bind("<<ComboboxSelected>>", self._on_transport_changed)

        self.device_label = ttk.Label(controls, text="Pico 1 COM:")
        self.device_label.grid(row=1, column=0, padx=(0, 4), pady=(8, 0), sticky="e")
        self.device_box = ttk.Combobox(
            controls, width=38, textvariable=self.port_name, state="readonly"
        )
        self.device_box.grid(
            row=1, column=1, columnspan=3, padx=(0, 4), pady=(8, 0), sticky="w"
        )
        self.refresh_button = ttk.Button(
            controls, text="Refresh", command=self._refresh_ports
        )
        self.refresh_button.grid(row=1, column=4, padx=(0, 4), pady=(8, 0))
        self.connect_button = ttk.Button(
            controls, text="Connect", command=self._toggle_connection
        )
        self.connect_button.grid(row=1, column=5, pady=(8, 0))

        ttk.Label(controls, text="PC C resolution:").grid(
            row=2, column=0, pady=(8, 0), padx=(0, 4), sticky="e"
        )
        ttk.Entry(controls, width=7, textvariable=self.target_width).grid(
            row=2, column=1, pady=(8, 0), sticky="w"
        )
        ttk.Label(controls, text="×").grid(row=2, column=2, pady=(8, 0))
        ttk.Entry(controls, width=7, textvariable=self.target_height).grid(
            row=2, column=3, pady=(8, 0), sticky="w"
        )
        self.forward_checkbox = ttk.Checkbutton(
            controls,
            text="Enable input forwarding",
            variable=self.forward_input,
            command=self._on_forward_toggle,
            state="disabled",
        )
        self.forward_checkbox.grid(
            row=2, column=4, columnspan=2, pady=(8, 0), sticky="w"
        )

        self.canvas = tk.Canvas(self.root, background="black", highlightthickness=0)
        self.canvas.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
        self._placeholder = self.canvas.create_text(
            0, 0, text="Open the USB capture device", fill="white", tags="placeholder"
        )
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind("<Motion>", self._on_mouse_motion)
        self.canvas.bind("<Leave>", self._on_canvas_leave)
        self.canvas.bind("<MouseWheel>", self._on_mouse_wheel)
        self.canvas.bind("<ButtonPress-4>", lambda event: self._send_wheel_at(event, 1))
        self.canvas.bind("<ButtonPress-5>", lambda event: self._send_wheel_at(event, -1))

        for event_button, hid_button in ((1, 1), (3, 2), (2, 3)):
            self.canvas.bind(
                f"<ButtonPress-{event_button}>",
                lambda event, value=hid_button: self._on_mouse_button(
                    event, value, True
                ),
            )
            self.canvas.bind(
                f"<ButtonRelease-{event_button}>",
                lambda event, value=hid_button: self._on_mouse_button(
                    event, value, False
                ),
            )

        ttk.Label(self.root, textvariable=self.status, anchor="w", padding=(8, 4)).grid(
            row=2, column=0, sticky="ew"
        )

    def _open_camera(self) -> None:
        try:
            index = int(self.camera_index.get())
            if not 0 <= index <= 15:
                raise ValueError
        except ValueError:
            messagebox.showerror(
                "Invalid camera index", "Enter a camera index from 0 to 15."
            )
            return

        self._stop_camera()
        self._last_frame = None
        self._image_rect = None
        self.canvas.delete("video")
        self.canvas.itemconfigure(self._placeholder, state="normal")
        self.camera = CameraCapture(index)
        self.camera.start()
        self.status.set(f"Opening DirectShow capture device at index {index}...")

    def _stop_camera(self) -> None:
        if self.camera is not None:
            self.camera.stop()
            self.camera = None

    def _refresh_ports(self) -> None:
        ports = sorted(port.device for port in list_ports.comports())
        self.device_box["values"] = ports
        if self.port_name.get() not in ports:
            self.port_name.set(ports[0] if ports else "")
        if (
            not ports
            and self.serial_port is None
            and self.transport_mode.get() == "USB CDC (Pico)"
        ):
            self.status.set("No serial ports found. Connect Pico 1 to PC B.")
        self._update_connection_controls()

    def _on_transport_changed(self, _event=None) -> None:
        if self.transport_mode.get() == "USB CDC (Pico)":
            self.device_label.configure(text="Pico 1 COM:")
            self.device_box.configure(textvariable=self.port_name)
            self.refresh_button.configure(
                text="Refresh", command=self._refresh_ports
            )
            self._refresh_ports()
        else:
            self.device_label.configure(text="ESP32-S3 BLE:")
            self.device_box.configure(textvariable=self.ble_device_name)
            self.device_box["values"] = tuple(self.ble_devices)
            self.refresh_button.configure(text="Scan BLE", command=self._scan_ble)
            if not self.ble_devices:
                self.status.set(f"Scan for {BLE_DEVICE_NAME}.")
            self._update_connection_controls()

    def _scan_ble(self) -> None:
        if self.ble_scanning or self._connection_busy():
            return
        self.ble_scanning = True
        self.ble_device_name.set("")
        self.status.set("Scanning for BLE devices...")
        self._update_connection_controls()
        try:
            self.ble_link.scan()
        except RuntimeError as exc:
            self.ble_scanning = False
            self.status.set(f"Could not start BLE scan: {exc}")
            self._update_connection_controls()

    def _poll_ble_events(self) -> None:
        if self.closing:
            return
        while True:
            try:
                event, value = self.ble_link.events.get_nowait()
            except queue.Empty:
                break

            if event == "scan_complete":
                devices = sorted(
                    value,
                    key=lambda device: (
                        (device.name or "").casefold(),
                        device.address.casefold(),
                    ),
                )
                self.ble_devices.clear()
                for device in devices:
                    name = device.name or "Unnamed BLE device"
                    label = f"{name} [{device.address}]"
                    self.ble_devices[label] = device
                labels = tuple(self.ble_devices)
                self.device_box["values"] = labels
                preferred = next(
                    (
                        label
                        for label in labels
                        if label.startswith(f"{BLE_DEVICE_NAME} [")
                    ),
                    labels[0] if labels else "",
                )
                self.ble_device_name.set(preferred)
                self.ble_scanning = False
                self.status.set(
                    f"Found {len(labels)} BLE device(s). "
                    f"Select {BLE_DEVICE_NAME}."
                    if labels
                    else "No BLE devices found. Check the ESP32-S3 power and range."
                )
                self._update_connection_controls()
            elif event == "scan_error":
                self.ble_scanning = False
                self.status.set(f"BLE scan failed: {value}")
                self._update_connection_controls()
            elif event == "connected":
                if self.closing:
                    self.ble_link.disconnect()
                    continue
                self.ble_connecting = False
                self.ble_ready = True
                self.active_transport = "ble"
                self._update_connection_controls()
                if not self._write_packet(protocol.release_all_packet()):
                    continue
                device_name = getattr(value, "name", None) or getattr(
                    value, "address", "ESP32-S3"
                )
                self.status.set(f"Connected to {device_name} over BLE GATT.")
            elif event == "connect_error":
                self._drop_ble(f"BLE connection failed: {value}")
            elif event == "write_error":
                self._drop_ble(f"BLE command write failed: {value}")
            elif event == "disconnected":
                if self.active_transport == "ble" or self.ble_connecting:
                    self._drop_ble("BLE connection lost.")
            elif event == "disconnect_error":
                self.status.set(f"BLE disconnect error: {value}")

        if not self.closing:
            self.root.after(50, self._poll_ble_events)

    def _connection_ready(self) -> bool:
        return self.serial_ready or self.ble_ready

    def _connection_busy(self) -> bool:
        return (
            self.serial_port is not None
            or self.ble_connecting
            or self.ble_scanning
        )

    def _update_connection_controls(self) -> None:
        busy = self._connection_busy()
        ready = self._connection_ready()
        self.transport_box.configure(state="disabled" if busy or ready else "readonly")
        self.device_box.configure(state="disabled" if busy or ready else "readonly")
        self.refresh_button.configure(
            state="disabled" if busy or ready else "normal"
        )
        if ready:
            self.connect_button.configure(text="Disconnect", state="normal")
        elif self.ble_connecting:
            self.connect_button.configure(text="Connecting...", state="disabled")
        elif self.ble_scanning:
            self.connect_button.configure(text="Connect", state="disabled")
        elif self.serial_port is not None:
            self.connect_button.configure(text="Cancel", state="normal")
        else:
            self.connect_button.configure(text="Connect", state="normal")
        self.forward_checkbox.configure(state="normal" if ready else "disabled")

    def _toggle_connection(self) -> None:
        if self.serial_port is not None:
            self._send_all_up()
            self._drop_serial("Disconnected.")
            return
        if self.ble_ready:
            self._send_all_up()
            self._drop_ble("Disconnected.")
            return
        if self.ble_connecting:
            return

        if self.transport_mode.get() == "USB CDC (Pico)":
            self._connect_serial()
        else:
            self._connect_ble()

    def _connect_serial(self) -> None:

        name = self.port_name.get()
        if not name:
            self.status.set("Select Pico 1's COM port first.")
            return

        try:
            port = serial.Serial(
                name,
                self.BAUD_RATE,
                timeout=0,
                write_timeout=0.2,
            )
            port.dtr = False
            self.serial_port = port
            self.serial_ready = False
            self.active_transport = "serial"
            self._update_connection_controls()
            self.status.set(f"Connected to {name}; initializing CDC...")
            self.root.after(300, lambda: self._finish_serial_connect(port))
        except (serial.SerialException, OSError) as exc:
            self.active_transport = None
            self._update_connection_controls()
            self.status.set(f"Could not open {name}: {exc}")

    def _connect_ble(self) -> None:
        label = self.ble_device_name.get()
        device = self.ble_devices.get(label)
        if device is None:
            self.status.set(f"Scan for and select {BLE_DEVICE_NAME} first.")
            return

        self.ble_connecting = True
        self.active_transport = "ble"
        self._update_connection_controls()
        self.status.set(
            f"Connecting to {getattr(device, 'name', None) or device.address}..."
        )
        try:
            self.ble_link.connect(device)
        except RuntimeError as exc:
            self._drop_ble(f"Could not start BLE connection: {exc}")

    def _finish_serial_connect(self, port) -> None:
        if self.closing or self.serial_port is not port:
            return
        self.serial_ready = True
        self._update_connection_controls()
        self._write_packet(protocol.release_all_packet())
        if self.serial_port is port:
            self.status.set(f"Connected to Pico 1 at {self.BAUD_RATE} baud.")

    def _drop_serial(self, status: str) -> None:
        port = self.serial_port
        self.serial_port = None
        self.serial_ready = False
        self.active_transport = None
        self.forward_input.set(False)
        self._reset_pointer_state()
        if self.closing:
            self._uninstall_mouse_hook()
        else:
            self.root.after_idle(self._uninstall_mouse_hook)
        if port is not None:
            try:
                port.close()
            except (serial.SerialException, OSError) as exc:
                status = f"{status} Serial close error: {exc}"
        self._update_connection_controls()
        self.status.set(status)

    def _drop_ble(self, status: str) -> None:
        self.ble_ready = False
        self.ble_connecting = False
        self.active_transport = None
        self.forward_input.set(False)
        self._reset_pointer_state()
        if self.closing:
            self._uninstall_mouse_hook()
        else:
            self.root.after_idle(self._uninstall_mouse_hook)
        self.ble_link.disconnect()
        self._update_connection_controls()
        self.status.set(status)

    def _write_packet(self, packet: bytes) -> bool:
        port = self.serial_port
        if self.serial_ready and port is not None:
            try:
                written = port.write(packet)
                if written != len(packet):
                    raise serial.SerialTimeoutException(
                        f"Only {written} of {len(packet)} bytes were written."
                    )
                return True
            except (serial.SerialException, OSError) as exc:
                self._drop_serial(f"CDC connection lost: {exc}")
                return False
        if self.ble_ready:
            if self.ble_link.send(packet):
                return True
            self._drop_ble("BLE command could not be queued.")
        return False

    def _send_heartbeat(self) -> None:
        if self.closing:
            return
        if self._connection_ready():
            self._write_packet(protocol.heartbeat_packet())
        self.root.after(self.HEARTBEAT_MS, self._send_heartbeat)

    def _send_all_up(self) -> None:
        if self._connection_ready():
            self._write_packet(protocol.release_all_packet())
        self._reset_pointer_state()

    def _on_forward_toggle(self) -> None:
        self._reset_pointer_state()
        if not self.forward_input.get():
            self._uninstall_mouse_hook()
            self._send_all_up()
        elif self._connection_ready():
            try:
                self._install_mouse_hook()
            except OSError as exc:
                self.status.set(f"Extra mouse-button forwarding unavailable: {exc}")
            else:
                self.status.set(
                    "Input forwarding enabled while the pointer is over the video."
                )

    def _is_forwarding(self) -> bool:
        return self.forward_input.get() and self._connection_ready()

    def _reset_pointer_state(self) -> None:
        self._inside_video = False
        self._last_normalized = None
        self._fractional_dx = 0.0
        self._fractional_dy = 0.0
        self._wheel_remainder = 0.0

    def _poll_camera(self) -> None:
        if self.closing:
            return
        if self.camera is not None:
            if self.camera.error:
                self.status.set(self.camera.error)
            frame = self.camera.latest_frame()
            if frame is not None:
                self._last_frame = frame
                self._render_frame(frame)
        self.root.after(self.CAMERA_POLL_MS, self._poll_camera)

    def _render_frame(self, frame) -> None:
        frame_height, frame_width = frame.shape[:2]
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()
        if canvas_width < 2 or canvas_height < 2:
            return

        scale = min(canvas_width / frame_width, canvas_height / frame_height)
        display_width = max(1, int(frame_width * scale))
        display_height = max(1, int(frame_height * scale))
        left = (canvas_width - display_width) / 2
        top = (canvas_height - display_height) / 2

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        if (display_width, display_height) != (frame_width, frame_height):
            rgb = cv2.resize(
                rgb, (display_width, display_height), interpolation=cv2.INTER_AREA
            )
        self._photo = ImageTk.PhotoImage(Image.fromarray(rgb))
        self.canvas.itemconfigure(self._placeholder, state="hidden")
        self.canvas.delete("video")
        self.canvas.create_image(
            left, top, image=self._photo, anchor="nw", tags="video"
        )
        self._image_rect = (left, top, display_width, display_height)

    def _on_canvas_resize(self, event) -> None:
        self.canvas.coords(self._placeholder, event.width / 2, event.height / 2)
        if self._last_frame is not None:
            self._render_frame(self._last_frame)

    def _point_to_normalized(self, x: float, y: float) -> tuple[float, float] | None:
        if self._image_rect is None:
            return None
        left, top, width, height = self._image_rect
        if width <= 0 or height <= 0:
            return None
        if x < left or y < top or x >= left + width or y >= top + height:
            return None
        return (x - left) / width, (y - top) / height

    def _target_dimensions(self) -> tuple[int, int] | None:
        try:
            width = int(self.target_width.get())
            height = int(self.target_height.get())
        except ValueError:
            self.status.set("Enter numeric PC C screen dimensions.")
            return None
        if not 1 <= width <= 32768 or not 1 <= height <= 32768:
            self.status.set("PC C screen dimensions must be between 1 and 32768.")
            return None
        return width, height

    def _send_motion_to(self, point: tuple[float, float]) -> None:
        if self._last_normalized is None:
            self._last_normalized = point
            return

        dimensions = self._target_dimensions()
        if dimensions is None:
            self._last_normalized = point
            return

        width, height = dimensions
        self._fractional_dx += (point[0] - self._last_normalized[0]) * width
        self._fractional_dy += (point[1] - self._last_normalized[1]) * height
        dx = math.trunc(self._fractional_dx)
        dy = math.trunc(self._fractional_dy)
        self._fractional_dx -= dx
        self._fractional_dy -= dy
        self._last_normalized = point

        while dx or dy:
            step_x = max(-32768, min(32767, dx))
            step_y = max(-32768, min(32767, dy))
            if not self._write_packet(protocol.mouse_move_packet(step_x, step_y)):
                return
            dx -= step_x
            dy -= step_y

    def _update_pointer(self, point: tuple[float, float]) -> None:
        if not self._inside_video:
            self._inside_video = True
            self._last_normalized = point
            return
        if self._is_forwarding():
            self._send_motion_to(point)
        else:
            self._last_normalized = point

    def _leave_video(self) -> None:
        if self._inside_video:
            self._send_all_up()
        else:
            self._reset_pointer_state()

    def _on_mouse_motion(self, event) -> None:
        point = self._point_to_normalized(event.x, event.y)
        if point is None:
            self._leave_video()
            return
        self._update_pointer(point)

    def _on_canvas_leave(self, _event) -> None:
        self._leave_video()

    def _on_mouse_button(self, event, button: int, pressed: bool):
        if not self._is_forwarding():
            return None
        if pressed:
            point = self._point_to_normalized(event.x, event.y)
            if point is None:
                return None
            self.canvas.focus_set()
            self._update_pointer(point)
        if not self._write_packet(protocol.mouse_button_packet(button, pressed)):
            return "break"
        return "break"

    def _install_mouse_hook(self) -> None:
        if self._mouse_hook_handle is not None:
            return
        if _USER32 is None or _KERNEL32 is None or _MOUSE_HOOK_PROC is None:
            raise OSError("Windows mouse hooks are unavailable on this platform.")

        hook_proc = _MOUSE_HOOK_PROC(self._on_windows_mouse_hook)
        handle = _USER32.SetWindowsHookExW(
            WH_MOUSE_LL, hook_proc, _KERNEL32.GetModuleHandleW(None), 0
        )
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._mouse_hook_proc = hook_proc
        self._mouse_hook_handle = handle

    def _uninstall_mouse_hook(self) -> None:
        if self._mouse_hook_handle is None or _USER32 is None:
            return
        if not _USER32.UnhookWindowsHookEx(self._mouse_hook_handle):
            error = ctypes.WinError(ctypes.get_last_error())
            self.status.set(f"Could not release the Windows mouse hook: {error}")
            return
        self._mouse_hook_handle = None
        self._mouse_hook_proc = None

    def _on_windows_mouse_hook(self, code: int, message: int, data_pointer: int) -> int:
        if (
            code >= 0
            and message in (WM_XBUTTONDOWN, WM_XBUTTONUP, WM_XBUTTONDBLCLK)
            and not self.closing
            and self._is_forwarding()
        ):
            mouse_data = ctypes.cast(
                data_pointer, ctypes.POINTER(_MouseHookData)
            ).contents
            xbutton = (mouse_data.mouse_data >> 16) & 0xFFFF
            if xbutton in (1, 2):
                try:
                    canvas_x = mouse_data.point.x - self.canvas.winfo_rootx()
                    canvas_y = mouse_data.point.y - self.canvas.winfo_rooty()
                except tk.TclError:
                    canvas_x = -1
                    canvas_y = -1
                point = self._point_to_normalized(canvas_x, canvas_y)
                if point is not None:
                    pressed = message != WM_XBUTTONUP
                    if pressed:
                        self.canvas.focus_set()
                    self._update_pointer(point)
                    self._write_packet(
                        protocol.mouse_button_packet(3 + xbutton, pressed)
                    )

        if _USER32 is None:
            return 0
        return _USER32.CallNextHookEx(
            self._mouse_hook_handle, code, message, data_pointer
        )

    def _send_wheel_units(self, amount: int, horizontal: bool = False) -> None:
        while amount:
            step = max(-127, min(127, amount))
            if not self._write_packet(
                protocol.mouse_wheel_packet(step, horizontal=horizontal)
            ):
                return
            amount -= step

    def _send_wheel_at(self, event, amount: int, horizontal: bool = False):
        if not self._is_forwarding():
            return None
        point = self._point_to_normalized(event.x, event.y)
        if point is None:
            return None
        self._update_pointer(point)
        self._send_wheel_units(amount, horizontal)
        return "break"

    def _on_mouse_wheel(self, event):
        if not self._is_forwarding():
            return None
        point = self._point_to_normalized(event.x, event.y)
        if point is None:
            return None
        self._update_pointer(point)
        horizontal = bool(event.state & 0x0001)
        self._wheel_remainder += event.delta / 120.0
        amount = math.trunc(self._wheel_remainder)
        self._wheel_remainder -= amount
        if amount:
            self._send_wheel_units(amount, horizontal)
        return "break"

    def _pointer_position_in_video(self) -> tuple[float, float] | None:
        pointer_x, pointer_y = self.root.winfo_pointerxy()
        canvas_x = pointer_x - self.canvas.winfo_rootx()
        canvas_y = pointer_y - self.canvas.winfo_rooty()
        return self._point_to_normalized(canvas_x, canvas_y)

    def _forward_key(self, event, pressed: bool):
        if not self._is_forwarding():
            return None
        point = self._pointer_position_in_video()
        if point is None:
            return None
        self._update_pointer(point)
        usage = keysym_to_usage(event.keysym)
        if usage is None:
            self.status.set(f"Unsupported key: {event.keysym}")
            return "break"
        self._write_packet(protocol.key_packet(usage, pressed))
        return "break"

    def _on_key_press(self, event):
        return self._forward_key(event, True)

    def _on_key_release(self, event):
        return self._forward_key(event, False)

    def _on_focus_out(self, _event) -> None:
        self.root.after_idle(self._send_all_up)

    def _on_window_unmap(self, _event) -> None:
        self._send_all_up()

    def _on_close(self) -> None:
        self.closing = True
        self._uninstall_mouse_hook()
        self._send_all_up()
        if self.serial_port is not None:
            self._drop_serial("Closed.")
        elif self.ble_ready or self.ble_connecting:
            self._drop_ble("Closed.")
        self.ble_link.close()
        self._stop_camera()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    RemoteKvmApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
