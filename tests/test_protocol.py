import struct
import unittest

import protocol
from keymap import keysym_to_usage


class ProtocolTests(unittest.TestCase):
    def test_frames_decode_across_partial_reads(self):
        move = protocol.mouse_move_packet(-321, 123)
        key = protocol.key_packet(0xE1, True)
        decoder = protocol.FrameDecoder()

        self.assertEqual(decoder.feed(move[:3]), [])
        self.assertEqual(
            decoder.feed(move[3:] + key),
            [
                protocol.Frame(protocol.MSG_MOUSE_MOVE, struct.pack("<hh", -321, 123)),
                protocol.Frame(protocol.MSG_KEY, bytes((0xE1, 1))),
            ],
        )

    def test_decoder_recovers_after_bad_checksum(self):
        bad = bytearray(protocol.heartbeat_packet())
        bad[-1] ^= 0x01
        decoder = protocol.FrameDecoder()

        self.assertEqual(
            decoder.feed(bytes(bad) + protocol.heartbeat_packet()),
            [protocol.Frame(protocol.MSG_HEARTBEAT, b"")],
        )

    def test_packet_validation(self):
        with self.assertRaises(ValueError):
            protocol.encode_frame(protocol.MSG_KEY, bytes(9))
        with self.assertRaises(ValueError):
            protocol.mouse_move_packet(32768, 0)
        with self.assertRaises(ValueError):
            protocol.mouse_button_packet(0, True)

    def test_mouse_wheel_directions_use_distinct_messages(self):
        vertical = protocol.FrameDecoder().feed(
            protocol.mouse_wheel_packet(1)
        )[0]
        horizontal = protocol.FrameDecoder().feed(
            protocol.mouse_wheel_packet(-1, horizontal=True)
        )[0]

        self.assertEqual(vertical.message_type, protocol.MSG_MOUSE_WHEEL)
        self.assertEqual(horizontal.message_type, protocol.MSG_MOUSE_HWHEEL)

    def test_common_and_japanese_keys_map_to_hid_usages(self):
        self.assertEqual(keysym_to_usage("a"), 0x04)
        self.assertEqual(keysym_to_usage("F12"), 0x45)
        self.assertEqual(keysym_to_usage("Control_L"), 0xE0)
        self.assertEqual(keysym_to_usage("Henkan"), 0x8A)
        self.assertIsNone(keysym_to_usage("unknown-key"))


if __name__ == "__main__":
    unittest.main()
