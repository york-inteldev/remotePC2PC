# ESP32-S3 BLE GATT to USB HID

This firmware implements the single-board Remote KVM input path:

```text
PC B application (BLE GATT client)
  └─ BLE custom GATT service ─ ESP32-S3 (BLE GATT server)
                                └─ USB keyboard/mouse HID ─ PC C
```

PC C receives USB HID and does not need Bluetooth. This differs from the legacy NodeMCU sketch in the parent directory, which sends BLE HID directly to PC C.

## Hardware and Arduino IDE

Connect the ESP32-S3 board's **native USB-OTG** connector to PC C during operation. Do not assume that a connector labeled USB is the native USB-OTG port; check the board schematic. If the board has a separate USB-UART connector, use it to upload. Boards such as the XIAO ESP32-S3 can instead use the same native USB connector for the ROM USB-Serial/JTAG download interface: temporarily connect it to the development PC and enter download mode before uploading, then reconnect it to PC C.

For a Seeed Studio XIAO ESP32-S3, connect its external 2.4 GHz U.FL antenna with USB power disconnected. Without the antenna, BLE reception can be extremely weak or unreliable.

Install the **esp32 by Espressif Systems** Arduino board package, version **3.0.7 or newer**, then select:

- Board: **ESP32S3 Dev Module** (or the exact ESP32-S3 board)
- USB Mode: **USB-OTG (TinyUSB)**
- USB CDC On Boot: **Disabled**
- Upload Mode: **UART0 / Hardware CDC** for a separate USB-UART connector or the ROM USB-Serial/JTAG interface

For XIAO ESP32-S3, select **XIAO_ESP32S3**, 8 MB flash, and the default 8 MB partition scheme. Its USB CDC On Boot default may be enabled, so explicitly disable it.

With Arduino-ESP32 3.0.7, the firmware exposes separate keyboard and mouse HID collections in one USB HID interface; Windows enumerates them as separate keyboard and mouse devices. ESP32-S3 USB-OTG and USB-Serial/JTAG share a PHY, so the USB-Serial/JTAG interface cannot be used concurrently with USB-OTG HID. This sketch does not start a USB serial console.

Open `remotePC2PC_ESP32S3_GATT_HID.ino` and upload it to the ESP32-S3. On PC C, connect the native USB-OTG port. The board advertises as `RemotePC2PC-S3`.

If the board remains in upload mode after flashing, disconnect and reconnect USB without holding BOOT. To flash again through the native connector when only HID is exposed, hold BOOT while resetting or reconnecting the board, then release BOOT and select the ROM USB-Serial/JTAG port. The running firmware does not expose USB CDC, so a TinyUSB CDC upload cannot automatically reset it into download mode.

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

The maximum current frame is 13 bytes, which fits within the default 20-byte ATT write payload. Message types and payloads are documented in the [PC_App README](../../../PC_App/README.md). The PC B application sends a heartbeat every 500 ms; the firmware releases all held inputs after 1.5 seconds without a heartbeat and immediately when BLE disconnects. After a heartbeat timeout, it ignores input commands until a valid heartbeat arrives; release-all remains available.

## Security and validation

The custom GATT service currently has **no authentication or encryption**. A nearby BLE central may be able to connect and inject input. Use only in a trusted, controlled environment; add pairing/bonding and peer authorization before deploying where nearby devices are untrusted.

The USB output is standard USB keyboard/mouse HID, making it more suitable for pre-boot input than BLE HID. BIOS/UEFI support still depends on the target PC and has not been verified on physical hardware. Build success alone does not verify BLE range, USB enumeration, keyboard layout, mouse behavior, or BIOS compatibility. Do not flash the existing Pico boards as part of this configuration.
