import unittest

from placement import DETAILS_HEIGHT, EDGE_MARGIN, place_widget


class PlacementTests(unittest.TestCase):
    def assert_inside(self, placement):
        left, top, right, bottom = placement.work_area
        self.assertGreaterEqual(placement.x, left + EDGE_MARGIN)
        self.assertGreaterEqual(placement.y, top + EDGE_MARGIN)
        self.assertLessEqual(placement.x + placement.width, right - EDGE_MARGIN)
        self.assertLessEqual(placement.y + placement.height, bottom - EDGE_MARGIN)

    def test_bottom_opens_above_and_keeps_badges_in_place(self):
        result = place_widget(2282, 1274, 270, 76, True, (0, 0, 2560, 1392))
        self.assertEqual(result.direction, "up")
        self.assertEqual(result.anchor, (2282, 1274))
        self.assertEqual(result.content_offset, DETAILS_HEIGHT)
        self.assert_inside(result)

    def test_space_below_opens_down(self):
        result = place_widget(200, 100, 270, 76, True, (0, 0, 2560, 1392))
        self.assertEqual(result.direction, "down")
        self.assertEqual(result.anchor, (200, 100))
        self.assertEqual(result.y, 100)
        self.assert_inside(result)

    def test_taskbar_is_excluded_from_available_space(self):
        # It fits the physical 1440px screen but crosses the 1392px work area.
        result = place_widget(200, 1210, 270, 76, True, (0, 0, 2560, 1392))
        self.assertEqual(result.direction, "up")
        self.assert_inside(result)

    def test_exact_bottom_margin_still_allows_down(self):
        result = place_widget(200, 1176, 270, 76, True, (0, 0, 2560, 1392))
        self.assertEqual(result.direction, "down")
        self.assert_inside(result)

    def test_collapse_and_reopen_do_not_drift(self):
        anchor = (2282, 1274)
        for _ in range(10):
            expanded = place_widget(*anchor, 270, 76, True, (0, 0, 2560, 1392))
            collapsed = place_widget(*expanded.anchor, 270, 76, False, (0, 0, 2560, 1392))
            self.assertEqual(collapsed.anchor, anchor)
            self.assertEqual(collapsed.height, 76)

    def test_secondary_monitor_left_of_primary(self):
        result = place_widget(-400, 950, 270, 76, True, (-1920, 0, 0, 1040))
        self.assertEqual(result.direction, "up")
        self.assertEqual(result.anchor, (-400, 950))
        self.assert_inside(result)

    def test_secondary_monitor_above_primary(self):
        result = place_widget(200, -100, 270, 76, True, (0, -1080, 1920, -40))
        self.assertEqual(result.direction, "up")
        self.assert_inside(result)

    def test_unplugged_monitor_position_is_moved_inside(self):
        result = place_widget(-2000, 2000, 270, 76, True, (0, 0, 1920, 1040))
        self.assert_inside(result)

    def test_small_work_area_repositions_whole_popup(self):
        result = place_widget(100, 90, 270, 76, True, (0, 0, 800, 300))
        self.assert_inside(result)


if __name__ == "__main__":
    unittest.main()
