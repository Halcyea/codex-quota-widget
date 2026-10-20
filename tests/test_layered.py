from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from PIL import Image

from app import PetWindow, WIDTH
from layered import premultiplied_bgra
from placement import place_widget


class PixelTests(unittest.TestCase):
    def test_partial_alpha_is_premultiplied_and_bgra(self):
        image=Image.new("RGBA",(1,1),(200,100,50,128))
        self.assertEqual(premultiplied_bgra(image),bytes((25,50,100,128)))

    def test_transparent_color_cannot_leave_a_halo(self):
        image=Image.new("RGBA",(1,1),(255,0,255,0))
        self.assertEqual(premultiplied_bgra(image),bytes((0,0,0,0)))

    def test_opaque_color_and_pixel_order_are_preserved(self):
        image=Image.new("RGBA",(2,1))
        image.putdata([(10,20,30,255),(40,50,60,255)])
        self.assertEqual(premultiplied_bgra(image),bytes((30,20,10,255,60,50,40,255)))


class FrameCommitTests(unittest.TestCase):
    def window(self, expanded):
        window=PetWindow.__new__(PetWindow)
        window.anchor_x,window.anchor_y=500,1274
        window.expanded=expanded
        window.dragging=False
        area=(0,0,2560,1380)
        window.work_area=lambda x,y:area
        window.placement=place_widget(500,1274,WIDTH,76,not expanded,area)
        window.canvas=SimpleNamespace(config=Mock())
        window.root=SimpleNamespace(geometry=Mock(),update_idletasks=Mock(
            side_effect=AssertionError("An intermediate resize must never be painted")))
        window.render_image=lambda:Image.new("RGBA",(WIDTH,window.height),(20,40,60,128))
        window.presenter=SimpleNamespace(present=Mock())
        return window

    def test_open_above_commits_only_complete_frame_at_final_origin(self):
        window=self.window(True)
        window.render()
        window.presenter.present.assert_called_once()
        frame,x,y=window.presenter.present.call_args.args
        self.assertEqual((x,y,frame.size),(500,1142,(270,208)))
        self.assertEqual(frame.getpixel((0,0))[3],128)
        self.assertEqual((window.anchor_x,window.anchor_y),(500,1274))
        window.root.update_idletasks.assert_not_called()

    def test_close_commits_at_saved_anchor_without_intermediate_paint(self):
        window=self.window(False)
        window.render()
        window.presenter.present.assert_called_once()
        frame,x,y=window.presenter.present.call_args.args
        self.assertEqual((x,y,frame.size),(500,1274,(270,76)))
        window.root.update_idletasks.assert_not_called()

    def test_release_paints_one_unpressed_frame(self):
        window=PetWindow.__new__(PetWindow)
        window.dragging=False
        window.closed=False
        window.hovering=True
        window.expanded=True
        window.placement=place_widget(500,1274,WIDTH,76,True,(0,0,2560,1380))
        window.drag_origin=(0,0,500,1142,30,174)
        window.render=Mock()
        window.pointer_up(None)
        self.assertFalse(window.expanded)
        self.assertIsNone(window.drag_origin)
        window.render.assert_called_once()


if __name__=="__main__":
    unittest.main()
