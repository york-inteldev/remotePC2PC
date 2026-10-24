#include <Arduino.h>

static const uint32_t UART_BAUD = 115200;
static const int UART_TX_PIN = 4;
static const int UART_RX_PIN = 5;

void setup() {
    Serial.begin(115200);
    Serial2.setTX(UART_TX_PIN);
    Serial2.setRX(UART_RX_PIN);
    Serial2.begin(UART_BAUD);
}

void loop() {
    while (Serial.available() > 0) {
        const int value = Serial.read();
        if (value >= 0) {
            Serial2.write(static_cast<uint8_t>(value));
        }
    }

    while (Serial2.available() > 0) {
        const int value = Serial2.read();
        if (value >= 0) {
            Serial.write(static_cast<uint8_t>(value));
        }
    }
}
