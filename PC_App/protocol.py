from __future__ import annotations

import struct
from dataclasses import dataclass

SYNC = b"\xA5\x5A"
MAX_PAYLOAD = 8

MSG_MOUSE_MOVE = 0x01
MSG_MOUSE_BUTTON = 0x02
MSG_MOUSE_WHEEL = 0x03
MSG_KEY = 0x04
MSG_RELEASE_ALL = 0x05
MSG_HEARTBEAT = 0x06
MSG_MOUSE_HWHEEL = 0x07


def encode_frame(message_type: int, payload: bytes = b"") -> bytes:
    if not 0 <= message_type <= 0xFF:
        raise ValueError("message_type must fit in one byte")
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"payload must not exceed {MAX_PAYLOAD} bytes")

    header = bytes((message_type, len(payload)))
    checksum = 0
    for value in header + payload:
        checksum ^= value
    return SYNC + header + payload + bytes((checksum,))


def mouse_move_packet(dx: int, dy: int) -> bytes:
    if not -32768 <= dx <= 32767 or not -32768 <= dy <= 32767:
        raise ValueError("mouse deltas must fit in signed 16-bit values")
    return encode_frame(MSG_MOUSE_MOVE, struct.pack("<hh", dx, dy))


def mouse_button_packet(button: int, pressed: bool) -> bytes:
    if not 1 <= button <= 5:
        raise ValueError("button must be between 1 and 5")
    return encode_frame(MSG_MOUSE_BUTTON, bytes((button, int(pressed))))


def mouse_wheel_packet(amount: int, horizontal: bool = False) -> bytes:
    if not -127 <= amount <= 127:
        raise ValueError("wheel amount must be between -127 and 127")
    message_type = MSG_MOUSE_HWHEEL if horizontal else MSG_MOUSE_WHEEL
    return encode_frame(message_type, struct.pack("b", amount))


def key_packet(usage: int, pressed: bool) -> bytes:
    if not 1 <= usage <= 0xFF:
        raise ValueError("HID usage must be between 1 and 255")
    return encode_frame(MSG_KEY, bytes((usage, int(pressed))))


def release_all_packet() -> bytes:
    return encode_frame(MSG_RELEASE_ALL)


def heartbeat_packet() -> bytes:
    return encode_frame(MSG_HEARTBEAT)


@dataclass(frozen=True)
class Frame:
    message_type: int
    payload: bytes


class FrameDecoder:
    _WAIT_SYNC_1 = 0
    _WAIT_SYNC_2 = 1
    _WAIT_TYPE = 2
    _WAIT_LENGTH = 3
    _WAIT_PAYLOAD = 4
    _WAIT_CHECKSUM = 5

    def __init__(self) -> None:
        self._reset()

    def _reset(self) -> None:
        self._state = self._WAIT_SYNC_1
        self._message_type = 0
        self._length = 0
        self._payload = bytearray()
        self._checksum = 0

    def _restart_from(self, value: int) -> None:
        self._reset()
        if value == SYNC[0]:
            self._state = self._WAIT_SYNC_2

    def feed(self, data: bytes) -> list[Frame]:
        frames: list[Frame] = []
        for value in data:
            if self._state == self._WAIT_SYNC_1:
                if value == SYNC[0]:
                    self._state = self._WAIT_SYNC_2
            elif self._state == self._WAIT_SYNC_2:
                if value == SYNC[1]:
                    self._state = self._WAIT_TYPE
                elif value != SYNC[0]:
                    self._state = self._WAIT_SYNC_1
            elif self._state == self._WAIT_TYPE:
                self._message_type = value
                self._checksum = value
                self._state = self._WAIT_LENGTH
            elif self._state == self._WAIT_LENGTH:
                if value > MAX_PAYLOAD:
                    self._restart_from(value)
                    continue
                self._length = value
                self._checksum ^= value
                self._payload.clear()
                self._state = self._WAIT_CHECKSUM if value == 0 else self._WAIT_PAYLOAD
            elif self._state == self._WAIT_PAYLOAD:
                self._payload.append(value)
                self._checksum ^= value
                if len(self._payload) == self._length:
                    self._state = self._WAIT_CHECKSUM
            else:
                if value == self._checksum:
                    frames.append(Frame(self._message_type, bytes(self._payload)))
                self._restart_from(value)
        return frames
