# PC B to PC C Remote KVM

PC B displays a UVC USB capture adapter and forwards mouse/keyboard events to PC C. Choose either the existing two-Pico USB CDC/UART path or the ESP32-S3 BLE GATT path. The final board presents separate USB keyboard and mouse HID interfaces to PC C.

## Connections

### Existing Pico UART configuration

```text
PC A (Remote Desktop client)
  └─ RDP session ───────────────────────────────> PC B (USB Host)
                                                    ├─ PC_App (window shown to PC A over RDP)
                                                    │   └─ displays captured PC C video
                                                    ├─ USB CDC ──> Pico 1 (USB Device)
                                                    │                └─ UART1, GP4/GP5 ──> Pico 2
                                                    │                                       └─ USB HID ──> PC C (USB Host)
                                                    └─ USB video <── HDMI-USB capture adapter <── HDMI ── PC C
```

USB wiring between the Pico boards:

| Pico 1 | Pico 2 |
| --- | --- |
| GP4 / UART1 TX | GP5 / UART1 RX |
| GP5 / UART1 RX | GP4 / UART1 TX |
| GND | GND |

Use 3.3 V UART logic only. Do not connect the two boards' VBUS/5 V lines together. Each Pico is powered by its own USB connection to its respective PC.

The PC_App window on PC B is viewed from PC A over RDP. The HDMI-USB capture adapter feeds PC C's HDMI output to PC_App; the adapter and Pico 1 connect to PC B, while Pico 2 presents USB HID to PC C. No Raspberry Pi 4 or Arduino Due is required.

### ESP32-S3 BLE GATT configuration

```text
PC A (Remote Desktop client)
  └─ RDP session ───────────────────────────────> PC B
                                                   ├─ PC_App (its window is shown to PC A over RDP)
                                                   │   ├─ displays the captured PC C video
                                                   │   └─ BLE GATT client
                                                   │       ── BLE ──> ESP32-S3 (BLE GATT server)
                                                   │                    └─ USB HID keyboard/mouse ──> PC C
                                                   └─ USB video <── HDMI-USB capture adapter <── HDMI ── PC C
```

PC A views PC C's HDMI output in the PC_App window on PC B through the RDP session. The HDMI-USB capture adapter connects PC C's HDMI output to PC B over USB, and PC_App displays that video. For control, PC_App sends input over BLE GATT to the ESP32-S3, which presents USB keyboard/mouse HID to PC C.

This is a separate firmware option; it does not use either Pico or the UART wiring. The ESP32-S3 GATT firmware and board setup are documented in [PC_to_PC_adaptor/ESP32-BLE/remotePC2PC_ESP32S3_GATT_HID/README.md](./PC_to_PC_adaptor/ESP32-BLE/remotePC2PC_ESP32S3_GATT_HID/README.md). The earlier [NodeMCU BLE-HID sketch](./PC_to_PC_adaptor/ESP32-BLE/README.md) remains available as a legacy BLE-HID-to-PC-C option, but it does not provide USB HID.

## Arduino IDE setup

Install the **Raspberry Pi Pico/RP2040** board package by Earle F. Philhower, III.

Upload each sketch to its own Pico:

1. Open `PC_to_PC_adaptor\pico_to_pico\pico1_cdc_uart_bridge\pico1_cdc_uart_bridge.ino`, select **Raspberry Pi Pico**, set **Tools > USB Stack > Pico SDK**, and upload it to Pico 1. Pico 1 connects to PC B.
2. Open `PC_to_PC_adaptor\pico_to_pico\pico2_uart_hid\pico2_uart_hid.ino`, select **Raspberry Pi Pico**, set **Tools > USB Stack > Adafruit TinyUSB**, and upload it to Pico 2. Pico 2 connects to PC C.
3. Wire GP4, GP5, and GND as shown above.

Pico 2 uses the Arduino-Pico Adafruit TinyUSB stack so it can register two separate HID interfaces. The USB CDC interface may also appear on PC C; it is not used by the application.

For the ESP32-S3 option, use the separate sketch and settings in its [firmware README](./PC_to_PC_adaptor/ESP32-BLE/remotePC2PC_ESP32S3_GATT_HID/README.md). In particular, select the board's native **USB-OTG (TinyUSB)** port for PC C and disable USB CDC on boot.

## PC B application

The PC B application's source, dependencies, tests, operating instructions, and protocol documentation are consolidated in [PC_App/README.md](./PC_App/README.md). The repository root remains the VS Code workspace, and the local `.venv` remains at the root so its path is unchanged.

The application message format, GATT UUIDs, and test commands are documented in [PC_App/README.md](./PC_App/README.md).
