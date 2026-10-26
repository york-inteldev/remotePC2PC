# ESP32 NodeMCU BLE HID firmware

## Overall design

This sketch turns an ESP32 NodeMCU into a bridge between the PC B application and PC C. PC B sends input events over a USB serial COM port; the ESP32 advertises as a BLE HID keyboard and mouse and sends those reports to PC C.

```text
PC B application
  -- USB serial (115200 baud) --> ESP32 NodeMCU
                                   -- BLE HID --> PC C
```

PC C receives BLE HID, not USB HID. PC C therefore needs Bluetooth LE, and input in BIOS/UEFI is not guaranteed. This is the legacy NodeMCU design; it differs from the ESP32-S3 design where PC B sends custom BLE GATT writes and the ESP32-S3 presents USB HID to PC C. See the [project README](../../../README.md) for current wiring and the [PC_App README](../../../PC_App/README.md) for application setup.

## Startup and pairing

[`setup()`](./remotePC2PC_ESP32_BLE.ino#L376) starts serial communication at 115200 baud and initializes a BLE HID device named `RemotePC2PC-BLE-HID`. It registers keyboard and mouse report formats, starts the HID service, and advertises it. PC C can discover and pair with this device.

The BLE security setup is in [`RemoteKvmSecurityCallbacks`](./remotePC2PC_ESP32_BLE.ino#L94) and `setup()`. It requests bonding, but does not require a passkey or verify the peer. This is the Just Works pairing method: the connection can be encrypted, but it does not provide MITM protection.

## Serial input protocol

PC B sends framed input messages:

```text
A5 5A | message type | payload length | payload | XOR checksum
```

The message constants are defined near the top of the [sketch](./remotePC2PC_ESP32_BLE.ino#L14). [`loop()`](./remotePC2PC_ESP32_BLE.ino#L409) reads serial bytes and [`consumeByte()`](./remotePC2PC_ESP32_BLE.ino#L316) uses a state machine to find the sync marker, read the message type and length, collect the payload, and validate the checksum. Invalid checksums and unsupported payload lengths are discarded.

[`processFrame()`](./remotePC2PC_ESP32_BLE.ino#L246) handles each valid message:

| Type | Meaning |
| --- | --- |
| `0x01` | Mouse movement: signed 16-bit X and Y deltas |
| `0x02` | Mouse button: button number and pressed/released state |
| `0x03` | Vertical wheel |
| `0x04` | Keyboard usage and pressed/released state |
| `0x05` | Release all keys and buttons |
| `0x06` | Heartbeat |
| `0x07` | Horizontal wheel |

Multi-byte values are little-endian. Mouse deltas and wheel values are relative rather than absolute positions.

## BLE HID reports

[`hidReportDescriptor`](./remotePC2PC_ESP32_BLE.ino#L26) describes the reports PC C receives. Report ID `1` is the keyboard; report ID `2` is the mouse.

- [`sendKeyboardReport()`](./remotePC2PC_ESP32_BLE.ino#L128) builds a keyboard report from the currently held keys. It supports modifier keys and up to six simultaneous ordinary keys. More than six ordinary keys produces the HID rollover indication.
- [`sendMouseReport()`](./remotePC2PC_ESP32_BLE.ino#L164) builds a report containing button state, relative movement, and vertical/horizontal scroll values.
- Mouse movement is accumulated by [`queueMouseMovement()`](./remotePC2PC_ESP32_BLE.ino#L195). [`takeMouseStep()`](./remotePC2PC_ESP32_BLE.ino#L181) splits it into signed 8-bit-sized steps, and [`flushMouseMovement()`](./remotePC2PC_ESP32_BLE.ino#L214) sends them at intervals of about 8 ms.
- [`publishReport()`](./remotePC2PC_ESP32_BLE.ino#L116) sends a report notification only while a BLE connection is active.

## Disconnect and stuck-input handling

PC B is expected to send a heartbeat every 500 ms. If no heartbeat arrives for 1.5 seconds, [`loop()`](./remotePC2PC_ESP32_BLE.ino#L409) calls [`releaseAll()`](./remotePC2PC_ESP32_BLE.ino#L234) to clear held keys and buttons. BLE connection events are handled by [`handleLinkEvent()`](./remotePC2PC_ESP32_BLE.ino#L361), which resets local input and parser state; on reconnection it sends empty reports.

## Using this firmware with the PC B application

This NodeMCU firmware expects serial input from PC B. The current application's BLE GATT mode is for ESP32-S3 and is not compatible with this sketch. To use this NodeMCU firmware, choose the serial connection mode and select the NodeMCU's COM port. The UI may still use a Pico-oriented label; the serial protocol and baud rate are shared. See the [PC_App README](../../../PC_App/README.md) for application setup and the [ESP32-BLE README](../README.md) for NodeMCU build and pairing instructions.
