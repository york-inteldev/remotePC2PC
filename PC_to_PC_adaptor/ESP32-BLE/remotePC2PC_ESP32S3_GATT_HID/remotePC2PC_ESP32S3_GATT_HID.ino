#include <Arduino.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <USB.h>
#include <USBHIDKeyboard.h>
#include <USBHIDMouse.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <string.h>

static const char *BLE_DEVICE_NAME = "RemotePC2PC-S3";
static const char *BLE_SERVICE_UUID =
    "8e4f4d10-5e3d-4a21-9f70-8c7e6d5b4a30";
static const char *BLE_COMMAND_UUID =
    "8e4f4d11-5e3d-4a21-9f70-8c7e6d5b4a30";

static const uint32_t HEARTBEAT_TIMEOUT_MS = 1500;
static const uint32_t MOUSE_REPORT_INTERVAL_MS = 2;
static const uint8_t MAX_PAYLOAD = 8;
static const int32_t MAX_PENDING_MOUSE_DELTA = 131071;
static const UBaseType_t RX_QUEUE_LENGTH = 256;

static const uint8_t MSG_MOUSE_MOVE = 0x01;
static const uint8_t MSG_MOUSE_BUTTON = 0x02;
static const uint8_t MSG_MOUSE_WHEEL = 0x03;
static const uint8_t MSG_KEY = 0x04;
static const uint8_t MSG_RELEASE_ALL = 0x05;
static const uint8_t MSG_HEARTBEAT = 0x06;
static const uint8_t MSG_MOUSE_HWHEEL = 0x07;

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

static USBHIDKeyboard keyboardHid;
static USBHIDMouse mouseHid;
static QueueHandle_t receivedBytes = nullptr;
static volatile bool bleConnected = false;
static volatile bool inputQueueOverflow = false;
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

class RemoteKvmCommandCallbacks : public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic *characteristic) override {
        const String value = characteristic->getValue();
        for (size_t i = 0; i < value.length(); ++i) {
            uint8_t byte = static_cast<uint8_t>(value[i]);
            if (receivedBytes == nullptr ||
                xQueueSend(receivedBytes, &byte, 0) != pdTRUE) {
                inputQueueOverflow = true;
                break;
            }
        }
    }
};

static void sendKeyboardReport() {
    KeyReport report = {};
    uint8_t keyCount = 0;
    bool overflow = false;

    for (uint16_t usage = 0xE0; usage <= 0xE7; ++usage) {
        if (pressedKeys[usage]) {
            report.modifiers |= static_cast<uint8_t>(1U << (usage - 0xE0));
        }
    }

    for (uint16_t usage = 0x04; usage <= 0xDF; ++usage) {
        if (pressedKeys[usage]) {
            if (keyCount < sizeof(report.keys)) {
                report.keys[keyCount++] = static_cast<uint8_t>(usage);
            } else {
                overflow = true;
            }
        }
    }

    if (overflow) {
        memset(report.keys, 0x01, sizeof(report.keys));
    }
    keyboardHid.sendReport(&report);
}

static void sendMouseReport(
    int8_t x,
    int8_t y,
    int8_t vertical,
    int8_t horizontal
) {
    mouseHid.move(x, y, vertical, horizontal);
}

static int8_t takeMouseStep(int32_t &delta) {
    if (delta > 127) {
        delta -= 127;
        return 127;
    }
    if (delta < -127) {
        delta += 127;
        return -127;
    }
    const int8_t step = static_cast<int8_t>(delta);
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

    sendMouseReport(
        takeMouseStep(pendingMouseX),
        takeMouseStep(pendingMouseY),
        0,
        0
    );
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
    mouseHid.buttons(0);
    sendKeyboardReport();
}

static void resetParser() {
    parserState = WAIT_SYNC_1;
    messageType = 0;
    payloadLength = 0;
    payloadIndex = 0;
    checksum = 0;
}

static void drainReceivedBytes() {
    if (receivedBytes != nullptr) {
        xQueueReset(receivedBytes);
    }
}

static int16_t readInt16LE(const uint8_t *data) {
    const uint16_t value = static_cast<uint16_t>(data[0]) |
                           (static_cast<uint16_t>(data[1]) << 8);
    return static_cast<int16_t>(value);
}

static uint8_t mouseButtonMask(uint8_t button) {
    switch (button) {
    case 1:
        return MOUSE_LEFT;
    case 2:
        return MOUSE_RIGHT;
    case 3:
        return MOUSE_MIDDLE;
    case 4:
        return MOUSE_BACKWARD;
    case 5:
        return MOUSE_FORWARD;
    default:
        return 0;
    }
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
            const uint8_t mask = mouseButtonMask(payload[0]);
            if (payload[1]) {
                mouseButtons |= mask;
            } else {
                mouseButtons &= static_cast<uint8_t>(~mask);
            }
            mouseHid.buttons(mouseButtons);
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
    drainReceivedBytes();
    resetParser();
    releaseAll();

    if (event == LINK_CONNECTED && bleConnected) {
        lastHeartbeatMs = millis();
        watchdogReleased = false;
    } else {
        watchdogReleased = true;
    }
}

void setup() {
    receivedBytes = xQueueCreate(RX_QUEUE_LENGTH, sizeof(uint8_t));
    if (receivedBytes == nullptr) {
        while (true) {
            delay(1000);
        }
    }

    keyboardHid.begin();
    mouseHid.begin();
    USB.begin();

    BLEDevice::init(BLE_DEVICE_NAME);
    BLEServer *server = BLEDevice::createServer();
    server->setCallbacks(new RemoteKvmServerCallbacks());

    BLEService *service = server->createService(BLE_SERVICE_UUID);
    BLECharacteristic *command = service->createCharacteristic(
        BLE_COMMAND_UUID,
        BLECharacteristic::PROPERTY_WRITE |
            BLECharacteristic::PROPERTY_WRITE_NR
    );
    command->setCallbacks(new RemoteKvmCommandCallbacks());
    service->start();

    BLEAdvertising *advertising = BLEDevice::getAdvertising();
    advertising->addServiceUUID(BLE_SERVICE_UUID);
    advertising->setScanResponse(true);
    BLEDevice::startAdvertising();

    lastHeartbeatMs = millis();
}

void loop() {
    handleLinkEvent();

    if (inputQueueOverflow) {
        inputQueueOverflow = false;
        drainReceivedBytes();
        resetParser();
        releaseAll();
        watchdogReleased = true;
    }

    uint8_t value = 0;
    while (receivedBytes != nullptr &&
           xQueueReceive(receivedBytes, &value, 0) == pdTRUE) {
        if (bleConnected) {
            consumeByte(value);
        }
    }

    if (bleConnected && !watchdogReleased &&
        millis() - lastHeartbeatMs > HEARTBEAT_TIMEOUT_MS) {
        releaseAll();
        watchdogReleased = true;
    }

    flushMouseMovement();
    delay(1);
}
