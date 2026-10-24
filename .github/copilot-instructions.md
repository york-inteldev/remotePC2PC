# Project Context

When continuing work on the Remote KVM design discussed in the previous chat, read [CHAT_CONTEXT.md](../CHAT_CONTEXT.md) for the decisions and handoff summary, then [README.md](../README.md) for the current wiring and setup instructions. Treat the source files and README as authoritative if they differ from the historical summary.

The hardware plan is two Raspberry Pi Pico boards: Pico 1 bridges USB CDC from PC B to UART1, and Pico 2 converts UART1 input to separate USB keyboard and mouse HID interfaces for PC C. Preserve the USB-stack and UART-pin requirements documented in the README when changing the firmware.
