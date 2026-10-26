# ESP32-S3 BLE GATT to USB HID

This firmware implements the single-board Remote KVM input path:

```text
PC B application (BLE GATT client)
  └─ BLE custom GATT service ─ ESP32-S3 (BLE GATT server)
                                └─ USB keyboard/mouse HID ─ PC C
```

PC C receives USB HID and does not need Bluetooth. This differs from the legacy NodeMCU sketch in the parent directory, which sends BLE HID directly to PC C.

## Hardware and Arduino IDE

Connect the ESP32-S3 board's **native USB-OTG** connector to PC C during operation. Do not assume that a connector labeled USB is the native USB-OTG port; check the board schematic. If the board has a separate USB-UART connector, use it to upload. If it exposes only native USB-OTG, temporarily connect that port to the development PC and select **Upload Mode: USB-OTG CDC (TinyUSB)** for flashing, then reconnect it to PC C.

Install the **esp32 by Espressif Systems** Arduino board package, version **3.0.7 or newer**, then select:

- Board: **ESP32S3 Dev Module** (or the exact ESP32-S3 board)
- USB Mode: **USB-OTG (TinyUSB)**
- USB CDC On Boot: **Disabled**
- Upload Mode: **UART0 / Hardware CDC** for a separate USB-UART upload connector, or **USB-OTG CDC (TinyUSB)** for boards using native USB to upload

The firmware registers separate USB keyboard and mouse HID interfaces. ESP32-S3 USB-OTG and USB-Serial/JTAG share a PHY, so the USB-Serial/JTAG interface cannot be used concurrently with USB-OTG HID. This sketch does not start a USB serial console.

Open `remotePC2PC_ESP32S3_GATT_HID.ino` and upload it to the ESP32-S3. On PC C, connect the native USB-OTG port. The board advertises as `RemotePC2PC-S3`.

## PC B application

Install the PC B application's requirements from the repository root, then run the application:

```powershell
py -3 -m pip install -r .\PC_App\requirements.txt
py -3 .\PC_App\pc_b_remote_kvm.py
```

See the [PC_App README](../../../PC_App/README.md) for full setup and operation instructions.

Choose **BLE GATT (ESP32-S3)**, press **Scan BLE**, select `RemotePC2PC-S3`, and press **Connect**. PC B must have Bluetooth LE enabled and be within radio range. Windows Bluetooth pairing is not required for this prototype. After connection, enable input forwarding in the PC B app.

## GATT protocol

The custom service exposes one command characteristic:

| Item | Value |
| --- | --- |
| Service UUID | `8e4f4d10-5e3d-4a21-9f70-8c7e6d5b4a30` |
| Command characteristic UUID | `8e4f4d11-5e3d-4a21-9f70-8c7e6d5b4a30` |
| Characteristic properties | Write, Write Without Response |

Each GATT write contains one complete frame in the existing format:

```text
A5 5A | message type | payload length | payload | XOR checksum
```

The maximum current frame is 13 bytes, which fits within the default 20-byte ATT write payload. Message types and payloads are documented in the [PC_App README](../../../PC_App/README.md). The PC B application sends a heartbeat every 500 ms; the firmware releases all held inputs after 1.5 seconds without a heartbeat and immediately when BLE disconnects.

## Security and validation

The custom GATT service currently has **no authentication or encryption**. A nearby BLE central may be able to connect and inject input. Use only in a trusted, controlled environment; add pairing/bonding and peer authorization before deploying where nearby devices are untrusted.

The USB output is standard USB keyboard/mouse HID, making it more suitable for pre-boot input than BLE HID. BIOS/UEFI support still depends on the target PC and has not been verified on physical hardware. Build success alone does not verify BLE range, USB enumeration, keyboard layout, mouse behavior, or BIOS compatibility. Do not flash the existing Pico boards as part of this configuration.
