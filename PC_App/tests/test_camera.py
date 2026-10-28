import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

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

    def test_poll_only_renders_new_frames_and_resize_renders_last_frame(self):
        app = RemoteKvmApp.__new__(RemoteKvmApp)
        app.closing = False
        app.root = Mock()
        app.camera = Mock()
        app.camera.error = None
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
