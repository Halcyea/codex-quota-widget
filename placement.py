"""Screen placement independent of Tk, including monitors with negative origins."""
from dataclasses import dataclass


DETAILS_HEIGHT = 132
EDGE_MARGIN = 8


@dataclass(frozen=True)
class Placement:
    x: int
    y: int
    width: int
    height: int
    content_offset: int
    direction: str
    work_area: tuple[int, int, int, int]

    @property
    def anchor(self):
        """The collapsed widget origin, unchanged when opening above it."""
        return self.x, self.y + self.content_offset


def clamp(value, lower, upper):
    return max(lower, min(value, max(lower, upper)))


def place_widget(x, y, width, base_height, expanded, work_area):
    """Prefer below; use above when the expanded window would cross the edge.

    The work area excludes the taskbar. If neither side has enough room on
    its own, move the complete expanded window just far enough to fit.
    """
    left, top, right, bottom = work_area
    left += EDGE_MARGIN
    top += EDGE_MARGIN
    right -= EDGE_MARGIN
    bottom -= EDGE_MARGIN
    x = clamp(x, left, right - width)
    y = clamp(y, top, bottom - base_height)
    height = base_height + (DETAILS_HEIGHT if expanded else 0)
    direction = "down"
    offset = 0
    if expanded and y + height > bottom:
        direction = "up"
        offset = DETAILS_HEIGHT
    window_y = clamp(y - offset, top, bottom - height)
    return Placement(x, window_y, width, height, offset, direction, work_area)
