# ESP32 BLE firmware options

This directory contains two alternatives:

- **ESP32-S3 BLE GATT to USB HID:** [firmware and setup](./remotePC2PC_ESP32S3_GATT_HID/README.md) for the requested PC B BLE GATT client -> ESP32-S3 -> PC C USB HID architecture.
- **Legacy ESP32 NodeMCU BLE HID:** the original firmware below receives serial input and acts as a BLE HID device for PC C. It does not provide USB HID.

## Legacy ESP32 NodeMCU BLE HID

Firmware for one HiLetgo ESP32 ESP-32S NodeMCU board. The board receives the existing RemotePC2PC serial protocol from PC B and presents keyboard and mouse input to PC C over Bluetooth LE HID over GATT (HOGP).

```text
PC B -- USB-UART serial COM --> ESP32 NodeMCU -- BLE HOGP --> PC C
```

This replaces both Pico boards for normal operating-system input. It does not provide a wired USB HID device to PC C.

## Hardware

The target is the ESP32-based HiLetgo NodeMCU board listed at [Amazon.co.jp](https://www.amazon.co.jp/dp/B0718T232Z). The listing describes an ESP32 ESP-32S board with Wi-Fi and Bluetooth. Confirm that the module on the board is ESP32-based; an ESP8266 NodeMCU does not support BLE.

- Connect the NodeMCU USB port to PC B. Its USB-UART bridge provides the COM port used by the PC B application. Install the bridge driver for the chip fitted to the board if Windows does not show a COM port.
- PC C must have Bluetooth LE and support BLE HID devices. Pair it with `RemotePC2PC-BLE-HID` in Windows Bluetooth settings.
- Keep PC B and PC C within BLE radio range. No UART wiring between boards is required.

## Build and upload

Install **Arduino IDE** and version **2.0.17** of the **esp32 by Espressif Systems** board package. Select **ESP32 Dev Module** and the NodeMCU COM port. The BLE HID classes used by this sketch are included with the Arduino-ESP32 core. This is the core version used for the build check.

Alternatively, with Arduino CLI:

```powershell
arduino-cli core update-index
arduino-cli core install esp32:esp32@2.0.17
arduino-cli compile --fqbn esp32:esp32:esp32 .\PC_to_PC_adaptor\ESP32-BLE\remotePC2PC_ESP32_BLE
arduino-cli upload --port COMx --fqbn esp32:esp32:esp32 .\PC_to_PC_adaptor\ESP32-BLE\remotePC2PC_ESP32_BLE
```

After upload:

1. Pair PC C with `RemotePC2PC-BLE-HID`.
2. On PC B, install and run the application using the [PC_App instructions](../../PC_App/README.md).
3. Select the NodeMCU COM port and connect. The existing application may still label this port as Pico 1; the serial protocol and 115200 baud setting are unchanged.

## Input protocol

The sketch reads the existing framed protocol from the USB-UART serial port at 115200 baud:

```text
A5 5A | message type | payload length | payload | XOR checksum
```

All multi-byte values are little-endian. Supported messages:

| Type | Payload |
| --- | --- |
| `01` mouse move | signed 16-bit `dx`, signed 16-bit `dy` |
| `02` mouse button | button `1..5`, pressed `0/1` |
| `03` vertical wheel | signed 8-bit delta |
| `04` keyboard | HID usage, pressed `0/1` |
| `05` release all | empty |
| `06` heartbeat | empty; expected every 500 ms |
| `07` horizontal wheel | signed 8-bit delta |

Keyboard and mouse reports use distinct HID report IDs in one BLE HID service. Relative mouse deltas are sent in signed 8-bit steps. Held inputs are released after 1.5 seconds without a heartbeat and when the BLE connection drops.

## Limitations and pairing security

- PC C receives BLE HID, not USB HID. Firmware/BIOS screens may not accept input before the operating system's Bluetooth stack is running. Keep a wired USB HID path if pre-boot control is required.
- Pairing uses BLE bonding with Just Works authentication (encryption without MITM protection). Pair only with the intended PC C in a trusted environment and remove the pairing if the device is lost.
- The NodeMCU firmware is in this directory; the PC B application is in [PC_App](../../PC_App/README.md).
- Compilation does not verify radio range, Windows pairing, report mapping, or physical input behavior. Test these with the actual board and PC C before relying on the device.
