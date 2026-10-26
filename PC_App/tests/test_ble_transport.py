import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ble_transport import BLE_COMMAND_UUID, BLE_SERVICE_UUID, BleGattClient


class _FakeCharacteristic:
    properties = ["write-without-response"]


class _FakeService:
    def get_characteristic(self, uuid):
        return _FakeCharacteristic() if uuid == BLE_COMMAND_UUID else None


class _FakeServices:
    def get_service(self, uuid):
        return _FakeService() if uuid == BLE_SERVICE_UUID else None


class _FakeBleakClient:
    instances = []

    def __init__(self, device, disconnected_callback, timeout):
        self.device = device
        self.disconnected_callback = disconnected_callback
        self.timeout = timeout
        self.services = _FakeServices()
        self.is_connected = False
        self.written_packets = []
        self.packet_written = threading.Event()
        self.instances.append(self)

    async def connect(self):
        self.is_connected = True

    async def write_gatt_char(self, characteristic, data, response):
        self.written_packets.append((characteristic, bytes(data), response))
        self.packet_written.set()

    async def disconnect(self):
        if self.is_connected:
            self.is_connected = False
            self.disconnected_callback(self)


class BleTransportTests(unittest.TestCase):
    def _next_event(self, client, expected, timeout=2.0):
        while True:
            event, value = client.events.get(timeout=timeout)
            if event == expected:
                return value
            if event.endswith("_error"):
                self.fail(f"Unexpected {event}: {value}")

    @patch("ble_transport.BleakScanner.discover", new_callable=AsyncMock)
    def test_scan_returns_discovered_devices(self, discover):
        device = SimpleNamespace(name="RemotePC2PC-S3", address="AA:BB:CC:DD:EE:FF")
        discover.return_value = [device]
        client = BleGattClient()

        try:
            client.scan()
            self.assertEqual(self._next_event(client, "scan_complete"), [device])
        finally:
            client.close()

    @patch("ble_transport.BleakClient", _FakeBleakClient)
    def test_connect_and_write_use_gatt_command_characteristic(self):
        _FakeBleakClient.instances.clear()
        device = SimpleNamespace(name="RemotePC2PC-S3", address="AA:BB:CC:DD:EE:FF")
        client = BleGattClient()

        try:
            client.connect(device)
            self.assertIs(self._next_event(client, "connected"), device)
            self.assertTrue(client.send(b"\xA5\x5A\x06\x00\x06"))

            fake_client = _FakeBleakClient.instances[-1]
            self.assertTrue(fake_client.packet_written.wait(timeout=2.0))
            self.assertEqual(len(fake_client.written_packets), 1)
            _, packet, response = fake_client.written_packets[0]
            self.assertEqual(packet, b"\xA5\x5A\x06\x00\x06")
            self.assertFalse(response)
        finally:
            client.close()

    def test_rejects_write_larger_than_default_att_payload(self):
        client = BleGattClient()
        self.assertFalse(client.send(bytes(21)))
        self.assertEqual(client.events.get_nowait()[0], "write_error")


if __name__ == "__main__":
    unittest.main()
