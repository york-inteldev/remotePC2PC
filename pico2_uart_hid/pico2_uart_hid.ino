#include <Arduino.h>
#include <Adafruit_TinyUSB.h>
#include <string.h>

static const uint32_t UART_BAUD = 115200;
static const int UART_TX_PIN = 4;
static const int UART_RX_PIN = 5;
static const uint8_t MAX_PAYLOAD = 8;

static const uint8_t MSG_MOUSE_MOVE = 0x01;
static const uint8_t MSG_MOUSE_BUTTON = 0x02;
static const uint8_t MSG_MOUSE_WHEEL = 0x03;
static const uint8_t MSG_KEY = 0x04;
static const uint8_t MSG_RELEASE_ALL = 0x05;
static const uint8_t MSG_HEARTBEAT = 0x06;
static const uint8_t MSG_MOUSE_HWHEEL = 0x07;

static const uint32_t HEARTBEAT_TIMEOUT_MS = 1500;

static const uint8_t keyboardReportDescriptor[] = {
    TUD_HID_REPORT_DESC_KEYBOARD()
};
static const uint8_t mouseReportDescriptor[] = {
    TUD_HID_REPORT_DESC_MOUSE()
};

static Adafruit_USBD_HID keyboardHid;
static Adafruit_USBD_HID mouseHid;

enum ParserState : uint8_t {
    WAIT_SYNC_1,
    WAIT_SYNC_2,
    WAIT_TYPE,
    WAIT_LENGTH,
    WAIT_PAYLOAD,
    WAIT_CHECKSUM
};

static ParserState parserState = WAIT_SYNC_1;
static uint8_t messageType = 0;
static uint8_t payloadLength = 0;
static uint8_t payloadIndex = 0;
static uint8_t checksum = 0;
static uint8_t payload[MAX_PAYLOAD];
static uint8_t mouseButtons = 0;
static uint8_t pressedKeys[256] = {0};
static uint32_t lastHeartbeatMs = 0;
static bool watchdogReleased = false;

static void sendMouseReport(int16_t x, int16_t y, int16_t vertical, int16_t horizontal) {
    if (mouseHid.ready()) {
        mouseHid.mouseReport(
            0,
            mouseButtons,
            static_cast<int8_t>(x),
            static_cast<int8_t>(y),
            static_cast<int8_t>(vertical),
            static_cast<int8_t>(horizontal)
        );
    }
}

static void sendKeyboardReport() {
    uint8_t modifiers = 0;
    uint8_t keys[6] = {0, 0, 0, 0, 0, 0};
    uint8_t keyCount = 0;
    bool overflow = false;

    for (uint16_t usage = 0xE0; usage <= 0xE7; ++usage) {
        if (pressedKeys[usage]) {
            modifiers |= static_cast<uint8_t>(1U << (usage - 0xE0));
        }
    }

    for (uint16_t usage = 0x04; usage <= 0xFF; ++usage) {
        if (usage >= 0xE0 && usage <= 0xE7) {
            continue;
        }
        if (pressedKeys[usage]) {
            if (keyCount < 6) {
                keys[keyCount++] = static_cast<uint8_t>(usage);
            } else {
                overflow = true;
            }
        }
    }

    if (overflow) {
        memset(keys, 0x01, sizeof(keys));
    }

    if (keyboardHid.ready()) {
        keyboardHid.keyboardReport(0, modifiers, keys);
    }
}

static void releaseAll() {
    mouseButtons = 0;
    memset(pressedKeys, 0, sizeof(pressedKeys));
    sendMouseReport(0, 0, 0, 0);
    sendKeyboardReport();
}

static void sendRelativeMotion(int16_t dx, int16_t dy) {
    while (dx != 0 || dy != 0) {
        const int16_t stepX = dx > 127 ? 127 : (dx < -127 ? -127 : dx);
        const int16_t stepY = dy > 127 ? 127 : (dy < -127 ? -127 : dy);
        sendMouseReport(stepX, stepY, 0, 0);
        dx -= stepX;
        dy -= stepY;
    }
}

static int16_t readInt16LE(const uint8_t *data) {
    const uint16_t value = static_cast<uint16_t>(data[0]) |
                           (static_cast<uint16_t>(data[1]) << 8);
    return static_cast<int16_t>(value);
}

static void processFrame() {
    switch (messageType) {
    case MSG_MOUSE_MOVE:
        if (payloadLength == 4) {
            sendRelativeMotion(readInt16LE(&payload[0]), readInt16LE(&payload[2]));
        }
        break;
    case MSG_MOUSE_BUTTON:
        if (payloadLength == 2 && payload[0] >= 1 && payload[0] <= 5 &&
            payload[1] <= 1) {
            const uint8_t mask = static_cast<uint8_t>(1U << (payload[0] - 1));
            if (payload[1]) {
                mouseButtons |= mask;
            } else {
                mouseButtons &= static_cast<uint8_t>(~mask);
            }
            sendMouseReport(0, 0, 0, 0);
        }
        break;
    case MSG_MOUSE_WHEEL:
        if (payloadLength == 1) {
            sendMouseReport(0, 0, static_cast<int8_t>(payload[0]), 0);
        }
        break;
    case MSG_MOUSE_HWHEEL:
        if (payloadLength == 1) {
            sendMouseReport(0, 0, 0, static_cast<int8_t>(payload[0]));
        }
        break;
    case MSG_KEY:
        if (payloadLength == 2 && payload[0] != 0 && payload[1] <= 1) {
            pressedKeys[payload[0]] = payload[1];
            sendKeyboardReport();
        }
        break;
    case MSG_RELEASE_ALL:
        if (payloadLength == 0) {
            releaseAll();
        }
        break;
    case MSG_HEARTBEAT:
        if (payloadLength == 0) {
            lastHeartbeatMs = millis();
            watchdogReleased = false;
        }
        break;
    default:
        break;
    }
}

static void restartParser(uint8_t lastByte) {
    parserState = (lastByte == 0xA5) ? WAIT_SYNC_2 : WAIT_SYNC_1;
}

static void consumeByte(uint8_t value) {
    switch (parserState) {
    case WAIT_SYNC_1:
        if (value == 0xA5) {
            parserState = WAIT_SYNC_2;
        }
        break;
    case WAIT_SYNC_2:
        if (value == 0x5A) {
            parserState = WAIT_TYPE;
        } else if (value != 0xA5) {
            parserState = WAIT_SYNC_1;
        }
        break;
    case WAIT_TYPE:
        messageType = value;
        checksum = value;
        parserState = WAIT_LENGTH;
        break;
    case WAIT_LENGTH:
        if (value > MAX_PAYLOAD) {
            restartParser(value);
            break;
        }
        payloadLength = value;
        payloadIndex = 0;
        checksum ^= value;
        parserState = value == 0 ? WAIT_CHECKSUM : WAIT_PAYLOAD;
        break;
    case WAIT_PAYLOAD:
        payload[payloadIndex++] = value;
        checksum ^= value;
        if (payloadIndex == payloadLength) {
            parserState = WAIT_CHECKSUM;
        }
        break;
    case WAIT_CHECKSUM:
        if (value == checksum) {
            processFrame();
        }
        restartParser(value);
        break;
    }
}

void setup() {
    if (!TinyUSBDevice.isInitialized()) {
        TinyUSBDevice.begin(0);
    }

    Serial.begin(115200);
    Serial2.setTX(UART_TX_PIN);
    Serial2.setRX(UART_RX_PIN);
    Serial2.begin(UART_BAUD);

    keyboardHid.setPollInterval(2);
    keyboardHid.setBootProtocol(HID_ITF_PROTOCOL_KEYBOARD);
    keyboardHid.setReportDescriptor(
        keyboardReportDescriptor, sizeof(keyboardReportDescriptor)
    );
    keyboardHid.setStringDescriptor("Remote KVM Keyboard");
    if (!keyboardHid.begin()) {
        Serial.println("Failed to register keyboard HID interface.");
        while (true) {
            delay(1000);
        }
    }

    mouseHid.setPollInterval(2);
    mouseHid.setBootProtocol(HID_ITF_PROTOCOL_MOUSE);
    mouseHid.setReportDescriptor(
        mouseReportDescriptor, sizeof(mouseReportDescriptor)
    );
    mouseHid.setStringDescriptor("Remote KVM Mouse");
    if (!mouseHid.begin()) {
        Serial.println("Failed to register mouse HID interface.");
        while (true) {
            delay(1000);
        }
    }

    if (TinyUSBDevice.mounted()) {
        TinyUSBDevice.detach();
        delay(10);
        TinyUSBDevice.attach();
    }

    lastHeartbeatMs = millis();
}

void loop() {
    while (Serial2.available() > 0) {
        consumeByte(static_cast<uint8_t>(Serial2.read()));
    }

    if (!watchdogReleased &&
        millis() - lastHeartbeatMs > HEARTBEAT_TIMEOUT_MS) {
        releaseAll();
        watchdogReleased = true;
    }
}
