"""Small pixel helpers for game observers. All rects are window-relative 0..1 floats."""

# Regions are downscaled before pixel loops so a 1-2 Hz observer stays cheap on Retina frames.
MAX_SAMPLE = 160


class Frame:
    def __init__(self, image):
        self.image = image.convert("RGB")
        self.width, self.height = self.image.size

    def _box(self, rect):
        x, y, w, h = rect
        if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 - x + 1e-9 and 0 < h <= 1 - y + 1e-9):
            raise ValueError("rect must be [x, y, w, h] inside 0..1: %r" % (rect,))
        return (round(x * self.width), round(y * self.height),
                max(round((x + w) * self.width), round(x * self.width) + 1),
                max(round((y + h) * self.height), round(y * self.height) + 1))

    def crop(self, rect):
        region = self.image.crop(self._box(rect))
        scale = MAX_SAMPLE / max(region.size)
        if scale < 1:
            region = region.resize((max(1, round(region.width * scale)), max(1, round(region.height * scale))))
        return region

    def mean_rgb(self, rect):
        pixels = _pixels(self.crop(rect))
        return tuple(round(sum(p[i] for p in pixels) / len(pixels)) for i in range(3))

    def ratio(self, rect, rgb, tol=40):
        """Share of pixels in rect within tol (per channel) of rgb."""
        pixels = _pixels(self.crop(rect))
        return sum(1 for p in pixels if _near(p, rgb, tol)) / len(pixels)

    def fill_ratio(self, rect, rgb, tol=40, axis="x"):
        """Fill level of a bar: the furthest column (or row, bottom-up) that has matching pixels."""
        region = self.crop(rect)
        w, h = region.size
        px = region.load()
        if axis == "x":
            hits = [x for x in range(w) if any(_near(px[x, y], rgb, tol) for y in range(h))]
            return (max(hits) + 1) / w if hits else 0.0
        hits = [y for y in range(h) if any(_near(px[x, y], rgb, tol) for x in range(w))]
        return (h - min(hits)) / h if hits else 0.0

    def find_color(self, rect, rgb, tol=40, min_ratio=0.002):
        """Centroid of matching pixels as window-relative [x, y], or None if too few match."""
        region = self.crop(rect)
        w, h = region.size
        px = region.load()
        hits = [(x, y) for y in range(h) for x in range(w) if _near(px[x, y], rgb, tol)]
        if len(hits) < max(1, min_ratio * w * h):
            return None
        rx, ry, rw, rh = rect
        cx = sum(x for x, _ in hits) / len(hits) + 0.5
        cy = sum(y for _, y in hits) / len(hits) + 0.5
        return [round(rx + rw * cx / w, 4), round(ry + rh * cy / h, 4)]


def _pixels(region):
    # getdata() is deprecated in Pillow 12; fall back for older versions.
    return list(getattr(region, "get_flattened_data", region.getdata)())


def _near(pixel, rgb, tol):
    return all(abs(pixel[i] - rgb[i]) <= tol for i in range(3))
