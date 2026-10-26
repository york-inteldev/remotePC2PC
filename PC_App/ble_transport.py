from __future__ import annotations

import asyncio
import concurrent.futures
import queue
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

BLE_DEVICE_NAME = "RemotePC2PC-S3"
BLE_SERVICE_UUID = "8e4f4d10-5e3d-4a21-9f70-8c7e6d5b4a30"
BLE_COMMAND_UUID = "8e4f4d11-5e3d-4a21-9f70-8c7e6d5b4a30"
BLE_MAX_WRITE_SIZE = 20
BLE_SCAN_TIMEOUT_SECONDS = 6.0
BLE_CONNECT_TIMEOUT_SECONDS = 10.0
MAX_QUEUED_COMMANDS = 128
_Result = TypeVar("_Result")


class BleGattClient:
    def __init__(self) -> None:
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._loop_ready = threading.Event()
        self._start_lock = threading.Lock()
        self._connected = threading.Event()
        self._client: BleakClient | None = None
        self._command_characteristic = None
        self._write_response = False
        self._write_queue: asyncio.Queue[bytes] | None = None
        self._writer_task: asyncio.Task[None] | None = None

    def scan(self) -> None:
        future = self._submit(
            BleakScanner.discover(timeout=BLE_SCAN_TIMEOUT_SECONDS)
        )
        future.add_done_callback(self._scan_completed)

    def connect(self, device: BLEDevice) -> None:
        future = self._submit(self._connect(device))
        future.add_done_callback(self._connect_completed)

    def send(self, packet: bytes) -> bool:
        if not packet:
            raise ValueError("BLE command packets cannot be empty")
        if len(packet) > BLE_MAX_WRITE_SIZE:
            self.events.put(
                (
                    "write_error",
                    f"BLE command is {len(packet)} bytes; the ATT limit is "
                    f"{BLE_MAX_WRITE_SIZE} bytes.",
                )
            )
            return False
        if not self._connected.is_set():
            return False

        loop = self._loop
        if loop is None or not loop.is_running():
            return False
        try:
            loop.call_soon_threadsafe(self._enqueue_packet, bytes(packet))
        except RuntimeError as exc:
            self.events.put(("write_error", str(exc)))
            return False
        return True

    def disconnect(self) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        future = asyncio.run_coroutine_threadsafe(self._disconnect(), loop)
        future.add_done_callback(self._disconnect_completed)

    def close(self) -> None:
        loop = self._loop
        thread = self._thread
        if loop is None or thread is None or not loop.is_running():
            return

        future = asyncio.run_coroutine_threadsafe(self._disconnect(), loop)
        try:
            future.result(timeout=2.0)
        except Exception as exc:
            self.events.put(("disconnect_error", str(exc)))
        finally:
            if loop.is_running():
                loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=2.0)

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._start_lock:
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run_loop,
                    name="RemoteKvmBle",
                    daemon=True,
                )
                self._thread.start()

        if not self._loop_ready.wait(timeout=5.0):
            raise RuntimeError("BLE event loop did not start.")
        if self._loop is None or not self._loop.is_running():
            raise RuntimeError("BLE event loop is not available.")
        return self._loop

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._loop_ready.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    def _submit(
        self, coroutine: Coroutine[Any, Any, _Result]
    ) -> concurrent.futures.Future[_Result]:
        loop = self._ensure_loop()
        return asyncio.run_coroutine_threadsafe(coroutine, loop)

    async def _connect(self, device: BLEDevice) -> None:
        if self._client is not None and self._client.is_connected:
            raise RuntimeError("A BLE device is already connected.")

        client = BleakClient(
            device,
            disconnected_callback=self._on_disconnected,
            timeout=BLE_CONNECT_TIMEOUT_SECONDS,
        )
        self._client = client
        try:
            await client.connect()
            service = client.services.get_service(BLE_SERVICE_UUID)
            if service is None:
                raise RuntimeError(
                    "The selected device does not expose the RemotePC2PC service."
                )
            characteristic = service.get_characteristic(BLE_COMMAND_UUID)
            if characteristic is None:
                raise RuntimeError(
                    "The selected device does not expose the RemotePC2PC "
                    "command characteristic."
                )

            properties = {str(value).lower() for value in characteristic.properties}
            if "write-without-response" in properties:
                self._write_response = False
            elif "write" in properties:
                self._write_response = True
            else:
                raise RuntimeError(
                    "The RemotePC2PC command characteristic is not writable."
                )

            self._command_characteristic = characteristic
            self._write_queue = asyncio.Queue(maxsize=MAX_QUEUED_COMMANDS)
            self._connected.set()
            self._writer_task = asyncio.create_task(
                self._write_packets(client, characteristic, self._write_queue)
            )
            self.events.put(("connected", device))
        except Exception:
            self._connected.clear()
            self._client = None
            self._command_characteristic = None
            if client.is_connected:
                await client.disconnect()
            raise

    async def _write_packets(
        self,
        client: BleakClient,
        characteristic,
        write_queue: asyncio.Queue[bytes],
    ) -> None:
        while True:
            packet = await write_queue.get()
            try:
                await client.write_gatt_char(
                    characteristic,
                    packet,
                    response=self._write_response,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.events.put(("write_error", str(exc)))
                self._discard_pending_packets(write_queue)
                asyncio.create_task(self._disconnect())
                return
            finally:
                write_queue.task_done()

    async def _disconnect(self) -> None:
        client = self._client
        write_queue = self._write_queue
        if client is None:
            return

        if write_queue is not None and client.is_connected:
            try:
                await asyncio.wait_for(write_queue.join(), timeout=1.0)
            except asyncio.TimeoutError:
                self.events.put(
                    (
                        "disconnect_error",
                        "Timed out while flushing queued BLE commands.",
                    )
                )

        if client.is_connected:
            await client.disconnect()

    def _enqueue_packet(self, packet: bytes) -> None:
        write_queue = self._write_queue
        if not self._connected.is_set() or write_queue is None:
            return
        try:
            write_queue.put_nowait(packet)
        except asyncio.QueueFull:
            self.events.put(
                ("write_error", "The BLE command queue is full; disconnecting.")
            )
            asyncio.create_task(self._disconnect())

    def _on_disconnected(self, client: BleakClient) -> None:
        if client is not self._client:
            return
        self._connected.clear()
        self._client = None
        self._command_characteristic = None
        writer_task = self._writer_task
        self._writer_task = None
        if writer_task is not None and not writer_task.done():
            writer_task.cancel()
        if self._write_queue is not None:
            self._discard_pending_packets(self._write_queue)
        self.events.put(("disconnected", None))

    def _scan_completed(self, future: concurrent.futures.Future) -> None:
        try:
            devices = future.result()
        except Exception as exc:
            self.events.put(("scan_error", str(exc)))
        else:
            self.events.put(("scan_complete", devices))

    def _connect_completed(self, future: concurrent.futures.Future) -> None:
        try:
            future.result()
        except Exception as exc:
            self.events.put(("connect_error", str(exc)))

    def _disconnect_completed(self, future: concurrent.futures.Future) -> None:
        try:
            future.result()
        except Exception as exc:
            self.events.put(("disconnect_error", str(exc)))

    @staticmethod
    def _discard_pending_packets(write_queue: asyncio.Queue[bytes]) -> None:
        while True:
            try:
                write_queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            else:
                write_queue.task_done()
