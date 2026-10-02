import tempfile
import unittest
from pathlib import Path

from common.render import BG, Canvas


class WatermarkTests(unittest.TestCase):
    def test_saved_poster_carries_a_faint_center_mark(self) -> None:
        canvas = Canvas()
        before = canvas.image.copy()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "poster.png"
            canvas.save(path)
            self.assertTrue(path.exists())
        after = canvas.image
        changed = 0
        peak = 0
        for y in range(0, after.height, 4):
            for x in range(0, after.width, 4):
                left = before.getpixel((x, y))
                right = after.getpixel((x, y))
                delta = abs(right[0] - left[0]) + abs(right[1] - left[1]) + abs(right[2] - left[2])
                changed += bool(delta)
                peak = max(peak, delta)
        self.assertGreater(changed, 1000)
        self.assertLess(peak, 90)
        self.assertEqual(after.getpixel((4, 20)), BG)
