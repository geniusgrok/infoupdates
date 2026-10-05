import tempfile
import unittest
from pathlib import Path

from PIL import ImageChops

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

    def test_custom_and_empty_watermarks(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            images = []
            for index, label in enumerate(('财经观察', '每日市场', '', '自定义财经观察名称' * 8)):
                canvas = Canvas(watermark=label)
                before = canvas.image.copy()
                canvas.save(Path(folder) / f'{index}.png')
                changed = ImageChops.difference(before, canvas.image).getbbox()
                if label:
                    self.assertIsNotNone(changed)
                    self.assertGreater(changed[0], 32)
                    self.assertGreater(changed[1], 32)
                    self.assertLess(changed[2], canvas.image.width - 32)
                    self.assertLess(changed[3], canvas.image.height - 32)
                else:
                    self.assertIsNone(changed)
                images.append(canvas.image)
            self.assertIsNotNone(ImageChops.difference(images[0], images[1]).getbbox())
