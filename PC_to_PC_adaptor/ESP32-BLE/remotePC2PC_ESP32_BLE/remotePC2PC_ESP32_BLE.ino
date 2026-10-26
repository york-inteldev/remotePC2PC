#include <Arduino.h>
#include <BLEDevice.h>
#include <BLEHIDDevice.h>
#include <BLEServer.h>
#include <BLESecurity.h>
#include <string.h>

static const uint32_t SERIAL_BAUD = 115200;
static const uint32_t HEARTBEAT_TIMEOUT_MS = 1500;
static const uint32_t MOUSE_REPORT_INTERVAL_MS = 8;
static const uint8_t MAX_PAYLOAD = 8;
static const int32_t MAX_PENDING_MOUSE_DELTA = 131071;

static const uint8_t MSG_MOUSE_MOVE = 0x01;
static const uint8_t MSG_MOUSE_BUTTON = 0x02;
static const uint8_t MSG_MOUSE_WHEEL = 0x03;
static const uint8_t MSG_KEY = 0x04;
static const uint8_t MSG_RELEASE_ALL = 0x05;
static const uint8_t MSG_HEARTBEAT = 0x06;
static const uint8_t MSG_MOUSE_HWHEEL = 0x07;

static const uint8_t REPORT_ID_KEYBOARD = 1;
static const uint8_t REPORT_ID_MOUSE = 2;
static const uint16_t BLE_HID_APPEARANCE_GENERIC = 0x03C0;

static uint8_t hidReportDescriptor[] = {
    0x05, 0x01, 0x09, 0x06, 0xA1, 0x01, 0x85, REPORT_ID_KEYBOARD,
    0x05, 0x07, 0x19, 0xE0, 0x29, 0xE7, 0x15, 0x00, 0x25, 0x01,
    0x75, 0x01, 0x95, 0x08, 0x81, 0x02,
    0x95, 0x01, 0x75, 0x08, 0x81, 0x01,
    0x95, 0x06, 0x75, 0x08, 0x15, 0x00, 0x25, 0xE7,
    0x19, 0x00, 0x29, 0xE7, 0x81, 0x00, 0xC0,
    0x05, 0x01, 0x09, 0x02, 0xA1, 0x01, 0x85, REPORT_ID_MOUSE,
    0x09, 0x01, 0xA1, 0x00,
    0x05, 0x09, 0x19, 0x01, 0x29, 0x05, 0x15, 0x00, 0x25, 0x01,
    0x95, 0x05, 0x75, 0x01, 0x81, 0x02,
    0x95, 0x01, 0x75, 0x03, 0x81, 0x03,
    0x05, 0x01, 0x09, 0x30, 0x09, 0x31, 0x09, 0x38,
    0x15, 0x81, 0x25, 0x7F, 0x75, 0x08, 0x95, 0x03, 0x81, 0x06,
    0x05, 0x0C, 0x0A, 0x38, 0x02,
    0x15, 0x81, 0x25, 0x7F, 0x75, 0x08, 0x95, 0x01, 0x81, 0x06,
    0xC0, 0xC0
};

enum ParserState : uint8_t {
    WAIT_SYNC_1,
    WAIT_SYNC_2,
    WAIT_TYPE,
    WAIT_LENGTH,
    WAIT_PAYLOAD,
    WAIT_CHECKSUM
};

enum LinkEvent : uint8_t {
    LINK_NONE,
    LINK_CONNECTED,
    LINK_DISCONNECTED
};

static BLEHIDDevice *hidDevice = nullptr;
static BLECharacteristic *keyboardInput = nullptr;
static BLECharacteristic *mouseInput = nullptr;
static volatile bool bleConnected = false;
static volatile uint8_t pendingLinkEvent = LINK_NONE;

static ParserState parserState = WAIT_SYNC_1;
static uint8_t messageType = 0;
static uint8_t payloadLength = 0;
static uint8_t payloadIndex = 0;
static uint8_t checksum = 0;
static uint8_t payload[MAX_PAYLOAD];

static uint8_t mouseButtons = 0;
static uint8_t pressedKeys[256] = {0};
static int32_t pendingMouseX = 0;
static int32_t pendingMouseY = 0;
static uint32_t lastMouseReportMs = 0;
static uint32_t lastHeartbeatMs = 0;
static bool watchdogReleased = false;

class RemoteKvmServerCallbacks : public BLEServerCallbacks {
    void onConnect(BLEServer *) override {
        bleConnected = true;
        pendingLinkEvent = LINK_CONNECTED;
    }

    void onDisconnect(BLEServer *) override {
        bleConnected = false;
        pendingLinkEvent = LINK_DISCONNECTED;
        BLEDevice::startAdvertising();
    }
};

class RemoteKvmSecurityCallbacks : public BLESecurityCallbacks {
    bool onSecurityRequest() override {
        return true;
    }

    uint32_t onPassKeyRequest() override {
        return 0;
    }

    void onPassKeyNotify(uint32_t) override {}

    bool onConfirmPIN(uint32_t) override {
        return true;
    }

#if defined(CONFIG_BLUEDROID_ENABLED)
    void onAuthenticationComplete(esp_ble_auth_cmpl_t) override {}
#elif defined(CONFIG_NIMBLE_ENABLED)
    void onAuthenticationComplete(ble_gap_conn_desc *) override {}
#endif
};

static void publishReport(
    BLECharacteristic *characteristic,
    uint8_t *report,
    size_t length
) {
    if (!bleConnected || characteristic == nullptr) {
        return;
    }
    characteristic->setValue(report, length);
    characteristic->notify();
}

static void sendKeyboardReport() {
    uint8_t modifiers = 0;
    uint8_t keys[6] = {0};
    uint8_t keyCount = 0;
    bool overflow = false;

    for (uint16_t usage = 0xE0; usage <= 0xE7; ++usage) {
        if (pressedKeys[usage]) {
            modifiers |= static_cast<uint8_t>(1U << (usage - 0xE0));
        }
    }

    for (uint16_t usage = 0x04; usage <= 0xE7; ++usage) {
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

    uint8_t report[9] = {
        REPORT_ID_KEYBOARD, modifiers, 0,
        keys[0], keys[1], keys[2], keys[3], keys[4], keys[5]
    };
    publishReport(keyboardInput, report, sizeof(report));
}

static void sendMouseReport(
    int16_t x,
    int16_t y,
    int8_t vertical,
    int8_t horizontal
) {
    uint8_t report[6] = {
        REPORT_ID_MOUSE,
        mouseButtons,
        static_cast<uint8_t>(x),
        static_cast<uint8_t>(y),
        static_cast<uint8_t>(vertical),
        static_cast<uint8_t>(horizontal)
    };
    publishReport(mouseInput, report, sizeof(report));
}

static int16_t takeMouseStep(int32_t &delta) {
    if (delta > 127) {
        delta -= 127;
        return 127;
    }
    if (delta < -127) {
        delta += 127;
        return -127;
    }
    const int16_t step = static_cast<int16_t>(delta);
    delta = 0;
    return step;
}

static void queueMouseMovement(int16_t dx, int16_t dy) {
    if (!bleConnected) {
        return;
    }

    pendingMouseX += dx;
    pendingMouseY += dy;
    if (pendingMouseX > MAX_PENDING_MOUSE_DELTA) {
        pendingMouseX = MAX_PENDING_MOUSE_DELTA;
    } else if (pendingMouseX < -MAX_PENDING_MOUSE_DELTA) {
        pendingMouseX = -MAX_PENDING_MOUSE_DELTA;
    }
    if (pendingMouseY > MAX_PENDING_MOUSE_DELTA) {
        pendingMouseY = MAX_PENDING_MOUSE_DELTA;
    } else if (pendingMouseY < -MAX_PENDING_MOUSE_DELTA) {
        pendingMouseY = -MAX_PENDING_MOUSE_DELTA;
    }
}

static void flushMouseMovement() {
    if (!bleConnected ||
        (pendingMouseX == 0 && pendingMouseY == 0) ||
        millis() - lastMouseReportMs < MOUSE_REPORT_INTERVAL_MS) {
        return;
    }

    const int16_t stepX = takeMouseStep(pendingMouseX);
    const int16_t stepY = takeMouseStep(pendingMouseY);
    sendMouseReport(stepX, stepY, 0, 0);
    lastMouseReportMs = millis();
}

static void clearInputState() {
    mouseButtons = 0;
    memset(pressedKeys, 0, sizeof(pressedKeys));
    pendingMouseX = 0;
    pendingMouseY = 0;
}

static void releaseAll() {
    clearInputState();
    sendMouseReport(0, 0, 0, 0);
    sendKeyboardReport();
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
            queueMouseMovement(
                readInt16LE(&payload[0]),
                readInt16LE(&payload[2])
            );
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
        if (payloadLength == 2 && payload[0] != 0 &&
            payload[0] <= 0xE7 && payload[1] <= 1) {
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

static void resetParser() {
    parserState = WAIT_SYNC_1;
    messageType = 0;
    payloadLength = 0;
    payloadIndex = 0;
    checksum = 0;
}

static void restartParser(uint8_t lastByte) {
    resetParser();
    if (lastByte == 0xA5) {
        parserState = WAIT_SYNC_2;
    }
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

static void handleLinkEvent() {
    const uint8_t event = pendingLinkEvent;
    if (event == LINK_NONE) {
        return;
    }
    pendingLinkEvent = LINK_NONE;
    clearInputState();
    resetParser();

    if (event == LINK_CONNECTED && bleConnected) {
        sendMouseReport(0, 0, 0, 0);
        sendKeyboardReport();
    }
}

void setup() {
    Serial.begin(SERIAL_BAUD);
    delay(200);

    BLEDevice::init("RemotePC2PC-BLE-HID");
    BLEDevice::setSecurityCallbacks(new RemoteKvmSecurityCallbacks());

    BLESecurity *security = new BLESecurity();
    security->setCapability(ESP_IO_CAP_NONE);
    security->setAuthenticationMode(ESP_LE_AUTH_REQ_SC_BOND);

    BLEServer *server = BLEDevice::createServer();
    server->setCallbacks(new RemoteKvmServerCallbacks());

    hidDevice = new BLEHIDDevice(server);
    hidDevice->manufacturer()->setValue("RemotePC2PC");
    hidDevice->hidInfo(0x00, 0x01);
    hidDevice->reportMap(hidReportDescriptor, sizeof(hidReportDescriptor));
    keyboardInput = hidDevice->inputReport(REPORT_ID_KEYBOARD);
    mouseInput = hidDevice->inputReport(REPORT_ID_MOUSE);
    hidDevice->startServices();

    BLEAdvertising *advertising = BLEDevice::getAdvertising();
    advertising->setAppearance(BLE_HID_APPEARANCE_GENERIC);
    advertising->addServiceUUID(hidDevice->hidService()->getUUID());
    advertising->setScanResponse(true);
    advertising->setMinPreferred(0x06);
    advertising->setMaxPreferred(0x12);
    BLEDevice::startAdvertising();

    lastHeartbeatMs = millis();
}

void loop() {
    handleLinkEvent();

    while (Serial.available() > 0) {
        const int value = Serial.read();
        if (value >= 0) {
            consumeByte(static_cast<uint8_t>(value));
        }
    }

    if (!watchdogReleased &&
        millis() - lastHeartbeatMs > HEARTBEAT_TIMEOUT_MS) {
        releaseAll();
        watchdogReleased = true;
    }

    flushMouseMovement();
}
