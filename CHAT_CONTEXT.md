# Remote KVM: Chat Handoff

This file preserves the relevant decisions and implementation status from the preceding chat so a new VS Code window or Copilot Chat session can continue the work. It is a handoff summary, not a verbatim transcript; the original chat remains in its original chat history.

## Goal and design history

- PC A connects to Windows PC B over RDP.
- PC C's HDMI output is captured by a USB capture adapter connected to PC B, so PC A can view PC C through PC B.
- PC C is on a separate network, so control is sent through USB rather than over the network.
- The first proposed control path used a Raspberry Pi 4, then Arduino Due was considered. The final decision was to use two Raspberry Pi Pico boards because of cost.
- Final input path: PC A -> RDP -> PC B -> USB CDC -> Pico 1 -> UART -> Pico 2 -> USB keyboard/mouse HID -> PC C.
- Video path: PC C HDMI -> USB capture adapter -> PC B capture window -> RDP -> PC A.

## Current project files

- `pc_b_remote_kvm.py`: PC B Tkinter/OpenCV capture window, serial connection, and mouse/keyboard forwarding.
- `protocol.py`: framed CDC/UART message protocol and event encoding.
- `keymap.py`: Tk key symbols to USB HID keyboard usages.
- `pico1_cdc_uart_bridge/pico1_cdc_uart_bridge.ino`: Pico 1 CDC-to-UART bridge.
- `pico2_uart_hid/pico2_uart_hid.ino`: Pico 2 UART parser and USB HID keyboard/mouse implementation.
- `tests/test_protocol.py`: protocol and keymap tests.
- `README.md`: installation, wiring, Arduino IDE settings, operation, and known limitations.
- `requirements.txt`: PC B Python dependencies.

## Hardware and firmware decisions

- PC B connects to Pico 1 over USB CDC. Pico 1 connects to Pico 2 over UART1. Pico 2 connects to PC C as a USB HID device.
- UART wiring: Pico 1 GP4/TX -> Pico 2 GP5/RX; Pico 1 GP5/RX <- Pico 2 GP4/TX; connect GND between boards. Do not connect the two USB VBUS/5 V rails together.
- Both sketches use `Serial2` with `setTX(4)` and `setRX(5)`, because GP4/GP5 are UART1 pins in the chosen Arduino-Pico core.
- Pico 1 must use the Arduino-Pico **Pico SDK** USB stack. Pico 2 must use **Adafruit TinyUSB** (`usbstack=tinyusb`) to expose the two HID interfaces.
- The CDC frame format is `A5 5A | message type | payload length | payload | XOR checksum`. The Pico 2 watchdog releases held input after 1.5 seconds without a heartbeat; PC B sends a heartbeat every 500 ms.
- Mouse movement is relative, not an absolute cursor-position report. The app tracks normalized coordinates within the video and scales movement using PC C's configured resolution. Pointer acceleration/settings on PC C can affect the resulting travel.
- Side mouse buttons are handled by a Windows low-level mouse hook while input forwarding is enabled; Tk does not provide the required `Button-8/9` bindings.

## Validation and remaining work

Completed in the preceding chat:

- Five Python unit tests passed.
- Python syntax and application import checks passed.
- Tk UI, video-region hit testing, and Windows side-button hook smoke tests passed.
- Both Arduino-Pico sketches compiled with the configured USB stacks.
- The generated project was copied to `C:\Users\良和\remote_access`; all eight project files matched the source copy by SHA-256.

Not verified on physical hardware: capture-device operation, UART wiring/data flow, HID enumeration on PC C, and end-to-end interaction through RDP. Do not describe these as hardware-tested.
