import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

import protocol
from pc_b_remote_kvm import CameraCapture, RemoteKvmApp


class CameraTests(unittest.TestCase):
    def test_capture_keeps_only_the_newest_unread_frame(self):
        camera = CameraCapture(0)
        frames = [np.full((2, 3, 3), value, dtype=np.uint8) for value in (1, 2)]
        capture = Mock()
        capture.isOpened.return_value = True

        def read():
            frame = frames.pop(0)
            if not frames:
                camera._stop.set()
            return True, frame

        capture.read.side_effect = read
        with patch("pc_b_remote_kvm.cv2.VideoCapture", return_value=capture):
            camera._capture()

        np.testing.assert_array_equal(
            camera.latest_frame(), np.full((2, 3, 3), 2, dtype=np.uint8)
        )
        self.assertIsNone(camera.latest_frame())
        capture.release.assert_called_once()

    def test_latest_frame_is_consumed_without_copying(self):
        camera = CameraCapture(0)
        frame = np.zeros((2, 3, 3), dtype=np.uint8)
        with camera._lock:
            camera._frame = frame

        self.assertIs(camera.latest_frame(), frame)
        self.assertIsNone(camera.latest_frame())

    def test_transient_read_failures_do_not_report_an_error(self):
        camera = CameraCapture(0)
        frame = np.zeros((2, 3, 3), dtype=np.uint8)
        results = [(False, None)] * (CameraCapture.MAX_READ_FAILURES - 1)
        results.append((True, frame))
        capture = Mock()
        capture.isOpened.return_value = True

        def read():
            if not results:
                camera._stop.set()
                return False, None
            return results.pop(0)

        capture.read.side_effect = read
        with patch("pc_b_remote_kvm.cv2.VideoCapture", return_value=capture):
            with patch.object(camera._stop, "wait", return_value=False):
                camera._capture()

        self.assertIsNone(camera.take_error())

    def test_persistent_read_failures_report_a_single_error(self):
        camera = CameraCapture(0)
        reads = [(False, None)] * (CameraCapture.MAX_READ_FAILURES + 1)
        capture = Mock()
        capture.isOpened.return_value = True

        def read():
            if len(reads) == 1:
                camera._stop.set()
            return reads.pop(0)

        capture.read.side_effect = read
        with patch("pc_b_remote_kvm.cv2.VideoCapture", return_value=capture):
            with patch.object(camera._stop, "wait", return_value=False):
                camera._capture()

        self.assertIn("stopped returning frames", camera.take_error())
        self.assertIsNone(camera.take_error())

    def test_key_release_uses_the_usage_sent_on_press(self):
        app = RemoteKvmApp.__new__(RemoteKvmApp)
        app._is_forwarding = Mock(return_value=True)
        app._pointer_position_in_video = Mock(return_value=(0.5, 0.5))
        app._update_pointer = Mock()
        app._write_packet = Mock(return_value=True)
        app._pressed_keys = {}

        # Shift+minus on a Japanese layout yields "equal"; releasing Shift
        # first turns the release event into "minus".
        app._forward_key(SimpleNamespace(keysym="equal", keycode=189), True)
        app._forward_key(SimpleNamespace(keysym="minus", keycode=189), False)

        self.assertEqual(
            [call.args[0] for call in app._write_packet.call_args_list],
            [protocol.key_packet(0x2E, True), protocol.key_packet(0x2E, False)],
        )
        self.assertEqual(app._pressed_keys, {})

    def test_poll_only_renders_new_frames_and_resize_renders_last_frame(self):
        app = RemoteKvmApp.__new__(RemoteKvmApp)
        app.closing = False
        app.root = Mock()
        app.camera = Mock()
        app.camera.take_error.return_value = None
        frame = np.zeros((2, 3, 3), dtype=np.uint8)
        app.camera.latest_frame.side_effect = [frame, None]
        app._last_frame = None
        app._render_frame = Mock()
        app.canvas = Mock()
        app._placeholder = 1

        app._poll_camera()
        app._poll_camera()
        app._render_frame.assert_called_once_with(frame)

        app._on_canvas_resize(SimpleNamespace(width=100, height=80))
        self.assertEqual(app._render_frame.call_count, 2)
        app._render_frame.assert_called_with(frame)

    def test_render_preserves_pixels_geometry_and_reuses_canvas_item(self):
        frame = np.random.default_rng(42).integers(
            0, 256, (8, 12, 3), dtype=np.uint8
        )
        for width, height in ((6, 6), (12, 8), (24, 16)):
            with self.subTest(width=width, height=height):
                app = RemoteKvmApp.__new__(RemoteKvmApp)
                app.canvas = Mock()
                app.canvas.winfo_width.return_value = width
                app.canvas.winfo_height.return_value = height
                app._placeholder = 1
                app._video = 2
                with patch("pc_b_remote_kvm.ImageTk.PhotoImage", side_effect=lambda image: image):
                    app._render_frame(frame)

                scale = min(width / 12, height / 8)
                display_size = (max(1, int(12 * scale)), max(1, int(8 * scale)))
                expected = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                if display_size != (12, 8):
                    expected = cv2.resize(
                        expected, display_size, interpolation=cv2.INTER_AREA
                    )
                np.testing.assert_array_equal(np.asarray(app._photo), expected)
                left = (width - display_size[0]) / 2
                top = (height - display_size[1]) / 2
                self.assertEqual(app._image_rect, (left, top, *display_size))
                app.canvas.coords.assert_called_once_with(2, left, top)
                app.canvas.itemconfigure.assert_any_call(
                    2, image=app._photo, state="normal"
                )
                app.canvas.create_image.assert_not_called()
                app.canvas.delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
