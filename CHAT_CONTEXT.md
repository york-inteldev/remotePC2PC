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

- `PC_App/pc_b_remote_kvm.py`: PC B Tkinter/OpenCV capture window, serial connection, and mouse/keyboard forwarding.
- `PC_App/ble_transport.py`: BLE GATT client transport for ESP32-S3.
- `PC_App/protocol.py`: framed CDC/UART/BLE message protocol and event encoding.
- `PC_App/keymap.py`: Tk key symbols to USB HID keyboard usages.
- `PC_App/tests/`: protocol, keymap, and BLE transport tests.
- `PC_App/requirements.txt`: PC B Python dependencies.
- `PC_App/README.md`: PC B application setup, operation, protocol, and test instructions.
- `PC_to_PC_adaptor/pico_to_pico/pico1_cdc_uart_bridge/pico1_cdc_uart_bridge.ino`: Pico 1 CDC-to-UART bridge.
- `PC_to_PC_adaptor/pico_to_pico/pico2_uart_hid/pico2_uart_hid.ino`: Pico 2 UART parser and USB HID keyboard/mouse implementation.
- `README.md`: project wiring and firmware setup overview.

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

## Follow-up: single-board BLE option and lower-cost boards (2026-10-24)

The user considered replacing the inter-Pico UART link with BLE and using one MCU as a BLE receiver for PC B and a USB HID keyboard/mouse for PC C. This is a proposed alternative architecture, not a change to the current project: the two-Pico UART wiring and firmware documented above and in `README.md` remain authoritative and unchanged.

### Board comparison

Prices below were observed on 2026-10-24; they are indicative, exclude shipping, and can change.

| Board | Observed price | Assessment |
| --- | ---: | --- |
| Raspberry Pi Pico 2 WH | ¥1,710 | Price baseline; RP2350, CYW43439, with headers. |
| Raspberry Pi Pico 2 W, no headers | ¥1,375 | Same MCU and wireless hardware as Pico 2 WH; saves ¥335. Lowest-risk cost reduction if GPIO headers are not needed. |
| Raspberry Pi Pico W, no headers | ¥1,240 | RP2040 and CYW43439; saves ¥470. The input-framing and USB HID workload should fit, but combined BLE + HID firmware still needs a build and hardware test. |
| Seeed XIAO ESP32S3 | ¥1,480 | BLE and USB HID are technically feasible, but the savings over Pico 2 WH are small and firmware migration is required. USB-OTG and USB-Serial-JTAG share a PHY, so USB serial debugging cannot be assumed to work alongside HID. |
| Generic CH582M board | About ¥1,199 for a marketplace listing | Exact board variant, Japanese radio certification, stable supply, and firmware integration were not verified; not recommended when reliability matters. |

The main recommendation is Pico 2 W without headers for a low-risk reduction. Pico W is the lowest-cost mainstream option worth considering. If a Pico 2 WH has already been purchased, replacing it solely to save ¥335 is not recommended.

Current Arduino-Pico documentation describes Bluetooth/BTstack support for Pico W. Some retailer material stating that Bluetooth was unavailable refers to the board's initial release; current software support does not by itself prove that the project's BLE and Adafruit TinyUSB HID functions work together, so that integration must be validated before deployment. Japanese certification identifiers found during the research were 020-240399 for Pico 2 W/WH, 008-220422 for Pico W, and 217-230892 for XIAO ESP32S3. Confirm the certification marking on the exact domestic product before using its radio.

### ESP32-S3 BLE GATT to USB HID implementation

- PC B now starts in BLE GATT mode and retains the original USB CDC/Pico path as a selectable option. `PC_App/ble_transport.py` uses Bleak on a background asyncio loop; the existing framed messages and application input handling remain shared.
- New ESP32-S3 firmware is in `PC_to_PC_adaptor/ESP32-BLE/remotePC2PC_ESP32S3_GATT_HID/remotePC2PC_ESP32S3_GATT_HID.ino`. It accepts framed command writes through a custom BLE GATT service and registers separate USB keyboard and mouse HID interfaces for PC C.
- The GATT service UUID is `8e4f4d10-5e3d-4a21-9f70-8c7e6d5b4a30`; its command characteristic UUID is `8e4f4d11-5e3d-4a21-9f70-8c7e6d5b4a30`. Existing framing is unchanged. The maximum frame is 13 bytes and fits the default ATT write payload.
- Select **BLE GATT (ESP32-S3)** in the PC B app, scan, select `RemotePC2PC-S3`, and connect. The original serial/UART option remains available. The new firmware and wiring/configuration notes are in `PC_to_PC_adaptor/ESP32-BLE/remotePC2PC_ESP32S3_GATT_HID/README.md`.
- The earlier NodeMCU BLE-HID sketch remains unchanged in `PC_to_PC_adaptor/ESP32-BLE/remotePC2PC_ESP32_BLE/`; it still sends BLE HID directly to PC C and is not the USB HID design.
- The existing two-Pico UART firmware and connected Pico boards were not changed or accessed.

### Validation and remaining work

- Nine Python protocol and BLE transport tests pass.
- Python syntax, application import, and PC B transport UI smoke checks pass.
- ESP32-S3 firmware compiles with Arduino-ESP32 3.0.7 for `ESP32S3 Dev Module`, USB Mode **USB-OTG (TinyUSB)**, and USB CDC On Boot **Disabled**.
- No firmware was uploaded, no BLE scan or hardware connection was performed, and USB HID enumeration/input on PC C is not verified. BIOS/UEFI input remains target-PC-dependent and requires a physical test.
- The custom GATT service currently has no authentication or encryption; use only in a trusted environment until peer security is added.

Product and software references:

- [Pico 2 WH](https://akizukidenshi.com/catalog/g/g130399/), [Pico 2 W](https://akizukidenshi.com/catalog/g/g130330/), and [Pico W](https://akizukidenshi.com/catalog/g/g117947/) listings at Akizuki Denshi.
- [Arduino-Pico Bluetooth documentation](https://github.com/earlephilhower/arduino-pico/blob/53f1d8ee31a9565a15731c5aad8a6297bdf70144/docs/bluetooth.rst).
- [XIAO ESP32S3](https://akizukidenshi.com/catalog/g/g118078/) listing and [Espressif USB Device Stack documentation](https://docs.espressif.com/projects/esp-usb/en/latest/esp32s3/usb_device.html).
