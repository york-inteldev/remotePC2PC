# PC B to PC C Remote KVM

PC B displays a UVC USB capture adapter and forwards mouse/keyboard events over USB CDC. Two Raspberry Pi Pico boards relay the data over UART; Pico 2 presents separate USB mouse and keyboard HID interfaces to PC C.

## Connections

```text
PC B (USB Host)
  └─ USB CDC ─ Pico 1 (USB Device)
                 └─ UART1, GP4/GP5 ─ Pico 2
                                      └─ USB HID ─ PC C (USB Host)
```

USB wiring between the Pico boards:

| Pico 1 | Pico 2 |
| --- | --- |
| GP4 / UART1 TX | GP5 / UART1 RX |
| GP5 / UART1 RX | GP4 / UART1 TX |
| GND | GND |

Use 3.3 V UART logic only. Do not connect the two boards' VBUS/5 V lines together. Each Pico is powered by its own USB connection to its respective PC.

The USB roles are **PC B = Host, Pico 1 = CDC Device** and **PC C = Host, Pico 2 = HID Device**. The USB capture adapter connects to PC B and receives PC C's HDMI output. No Raspberry Pi 4 or Arduino Due is required.

## Arduino IDE setup

Install the **Raspberry Pi Pico/RP2040** board package by Earle F. Philhower, III.

Upload each sketch to its own Pico:

1. Open `pico1_cdc_uart_bridge\pico1_cdc_uart_bridge.ino`, select **Raspberry Pi Pico**, set **Tools > USB Stack > Pico SDK**, and upload it to Pico 1. Pico 1 connects to PC B.
2. Open `pico2_uart_hid\pico2_uart_hid.ino`, select **Raspberry Pi Pico**, set **Tools > USB Stack > Adafruit TinyUSB**, and upload it to Pico 2. Pico 2 connects to PC C.
3. Wire GP4, GP5, and GND as shown above.

Pico 2 uses the Arduino-Pico Adafruit TinyUSB stack so it can register two separate HID interfaces. The USB CDC interface may also appear on PC C; it is not used by the application.

## PC B application

Requirements: Windows, Python 3.10 or newer, and a UVC-compatible USB capture adapter.

From PowerShell, in this folder:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe .\pc_b_remote_kvm.py
```

Choose the capture-device index and press **Open camera**. OpenCV uses the Windows DirectShow backend. Camera indices depend on the PC, so try `0`, `1`, and so on. In Windows Settings, allow desktop apps to access the camera if needed.

Select Pico 1's COM port and press **Connect**. The app opens it at 115200 baud. The USB CDC baud setting is virtual; Pico 1's inter-board UART is configured for 115200 baud.

Set **PC C resolution** to PC C's active display resolution, then enable **Enable input forwarding**. Mouse and keyboard events are forwarded only while the pointer is over the displayed video image; controls around the video do not forward input. Left, right, middle, vertical/horizontal wheel, and Windows XButton1/XButton2 events are supported. The app installs a Windows low-level mouse hook for the two side buttons only while forwarding is enabled and passes those events through to PC B. Leaving the video, minimizing or deactivating the app, disabling forwarding, or losing the serial connection releases held keys and buttons.

Mouse positions in the displayed video are normalized to `[0, 1]`. Changes between successive normalized positions are scaled by PC C's configured width and height and sent as relative mouse movement. This is not absolute pointer positioning: the app cannot know PC C's current cursor position, and PC C's pointer speed/acceleration settings affect the resulting movement. The first pointer position after entering the video establishes a reference and does not reposition the cursor.

Keyboard events are sent as USB HID usages. PC C's keyboard layout determines the resulting characters. Common keys, modifiers, function/keypad keys, and Japanese IME keys are mapped.

## CDC protocol

Pico 1 transparently forwards CDC bytes to Pico 2 over UART. All multi-byte values are little-endian. Each application frame is:

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

Pico 2 releases all keys and buttons if no heartbeat arrives for 1.5 seconds.

## Validation

Run the dependency-free protocol and key mapping tests with:

```powershell
py -3 -m unittest discover -s .\tests -v
```
