# PC B Remote KVM Application

The PC-side application displays a UVC capture device connected to PC B and forwards mouse and keyboard input to PC C. Its Python source, dependency manifest, tests, operating instructions, and message protocol are kept together in this directory.

The repository root remains the VS Code workspace. The local `.venv` stays at the repository root so an existing interpreter selection is not moved; it is machine-specific and excluded from Git. Hardware wiring and firmware setup remain in the [root README](../README.md).

## Requirements and setup

- Windows
- Python 3.10 or newer
- A UVC-compatible USB capture adapter connected to PC B
- Bluetooth LE on PC B when using the ESP32-S3 BLE GATT transport

Run these commands in PowerShell from the repository root:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\PC_App\requirements.txt
.\.venv\Scripts\python.exe .\PC_App\pc_b_remote_kvm.py
```

If the root `.venv` already exists, skip the first command. The application requirements are listed in [`requirements.txt`](./requirements.txt).

## VS Code workspace and tasks

Open the repository root as the workspace so repository-level instructions and settings remain available. The root `.venv` has not been moved. The application entry point is now `${workspaceFolder}/PC_App/pc_b_remote_kvm.py`; a VS Code task that launches it should use that path and keep `${workspaceFolder}/.venv/Scripts/python.exe` as its interpreter.

No repository-local `.vscode` settings, task definitions, launch configuration, or `.code-workspace` file existed when the application was reorganized, so there were no such files to move or rewrite.

## Starting and operating the application

Run the application with the command above. Choose the capture-device index and press **Open camera**. OpenCV uses the Windows DirectShow backend. Camera indices depend on the PC; try `0`, `1`, and so on. In Windows Settings, allow desktop apps to access the camera if needed.

The application starts in **BLE GATT (ESP32-S3)** mode. Select the transport that matches the firmware:

- **USB CDC (Pico):** select Pico 1's COM port and press **Connect**. The app opens it at 115200 baud. This serial protocol can also be used with the legacy NodeMCU firmware; select its USB-UART COM port even if the UI label refers to Pico 1.
- **BLE GATT (ESP32-S3):** press **Scan BLE**, select `RemotePC2PC-S3`, then press **Connect**. The app writes each protocol frame to the ESP32-S3 command characteristic. This prototype does not require Windows Bluetooth pairing.

For board-specific wiring and setup, see the [ESP32-S3 firmware instructions](../PC_to_PC_adaptor/ESP32-BLE/remotePC2PC_ESP32S3_GATT_HID/README.md), the [legacy NodeMCU instructions](../PC_to_PC_adaptor/ESP32-BLE/README.md), or the [root README](../README.md) for the Pico UART setup.

Set **PC C resolution** to PC C's active display resolution, then enable **Enable input forwarding**. Mouse and keyboard events are forwarded only while the pointer is over the displayed video image; controls around the video do not forward input. Left, right, middle, vertical/horizontal wheel, and Windows XButton1/XButton2 events are supported. The app installs a Windows low-level mouse hook for the two side buttons only while forwarding is enabled and passes those events through to PC B.

Leaving the video, minimizing or deactivating the app, disabling forwarding, or losing the control connection releases held keys and buttons.

Mouse positions in the displayed video are normalized to `[0, 1]`. Changes between successive normalized positions are scaled by PC C's configured width and height and sent as relative mouse movement. This is not absolute pointer positioning: the app cannot know PC C's current cursor position, and PC C's pointer speed/acceleration settings affect the resulting movement. The first pointer position after entering the video establishes a reference and does not reposition the cursor.

Keyboard events are sent as USB HID usages. PC C's keyboard layout determines the resulting characters. Common keys, modifiers, function/keypad keys, and Japanese IME keys are mapped.

## Performance and implementation choice

The capture thread keeps only the newest unread frame; the UI draws each captured
frame at most once and redraws the last frame when the window is resized.
Scaling happens before BGR-to-RGB conversion, and the canvas image item is
reused. This avoids unnecessary frame copies and work on pixels that will not
be displayed. The UI still polls approximately every 33 ms, so this is not a
guarantee of 30 FPS or a bound on end-to-end latency.

On one Windows development PC, a synthetic 1920x1080 to 960x540 OpenCV
conversion benchmark (100 iterations, excluding capture, Tk display, RDP and
device I/O) had median times of 5.269 ms for the previous copy/convert/resize
sequence and 1.480 ms for resize/convert. The output pixels were identical for
that input. These numbers are illustrative, not hardware or end-to-end results.

A C# rewrite is not currently justified by this measurement: OpenCV already
performs the pixel operations in native code, while capture, Tk display, RDP,
and USB/BLE latency have not been measured on the target system. First measure
frame age and input-to-response latency with the actual capture device and both
ends of the RDP connection. If UI rendering or capture is then the bottleneck,
a Windows-specific C# prototype using an appropriate native video pipeline
could be compared under the same conditions before replacing the application.
Retain the existing framed protocol and release/heartbeat behavior in any port.

## Input protocol

The Pico 1 firmware transparently forwards USB CDC bytes to Pico 2 over UART. The ESP32-S3 firmware receives the same framed bytes as one BLE GATT write per frame. All multi-byte values are little-endian. Each application frame is:

```text
A5 5A | message type | payload length | payload | XOR checksum
```

The checksum is the XOR of the message type, payload length, and every payload byte.

| Type | Payload |
| --- | --- |
| `01` mouse move | signed 16-bit `dx`, signed 16-bit `dy` |
| `02` mouse button | button `1..5`, pressed `0/1` |
| `03` vertical wheel | signed 8-bit delta |
| `04` keyboard | USB HID usage, pressed `0/1` |
| `05` release all | empty |
| `06` heartbeat | empty; sent every 500 ms |
| `07` horizontal wheel | signed 8-bit delta |

The maximum current frame is 13 bytes, which fits within the default 20-byte ATT write payload. The Pico 2 and ESP32-S3 release held inputs after 1.5 seconds without a heartbeat; the ESP32-S3 also releases inputs on BLE disconnect.

For the legacy NodeMCU firmware, PC C receives BLE HID rather than USB HID. BIOS/UEFI input is not guaranteed because Bluetooth support may not be available before the operating system starts.

## Tests

Run the tests from PowerShell at the repository root:

```powershell
Push-Location .\PC_App
try {
    & ..\.venv\Scripts\python.exe -m unittest discover -s .\tests -v
} finally {
    Pop-Location
}
```
