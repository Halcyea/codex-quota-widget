"""额度挂件：独立的 Windows Codex 额度悬浮组件。"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import queue
import threading
import time
import tkinter as tk

from PIL import Image, ImageDraw, ImageFont, ImageTk

from quota import QuotaError, RpcClient, Snapshot, Window, discover_codex, mood
from placement import DETAILS_HEIGHT, place_widget
from layered import LayeredPresenter

BASE = Path(__file__).resolve().parent
RUNTIME = BASE / ".runtime"
SETTINGS_PATH = RUNTIME / "settings.json"
TRANSPARENT = "#ff00ff"
SHANGHAI = timezone(timedelta(hours=8))
TITLE = "额度挂件 · Codex Quota Widget"
WIDTH = 270
CARD_WIDTH = 124
CARD_HEIGHT = 46
CARD_RADIUS = 16
DETAIL_RADIUS = 18
LABEL_SIZE = 13
VALUE_SIZE = 18
FONT_REGULAR = BASE / "assets/fonts/MiSans-Regular.ttf"
FONT_SEMIBOLD = BASE / "assets/fonts/MiSans-Semibold.ttf"
REFRESH_SECONDS = 90
STALE_SECONDS = 180


def local_time(timestamp, fmt="%m/%d %H:%M") -> str:
    if timestamp is None:
        return "未提供"
    return datetime.fromtimestamp(timestamp, SHANGHAI).strftime(fmt)


def window_text(window: Window | None, snapshot: Snapshot | None, now=None) -> str:
    now = time.time() if now is None else now
    if snapshot is None or now - snapshot.captured_at > STALE_SECONDS:
        return "—"
    if window is None or window.expired(now):
        return "—"
    number = f"{window.remaining:.1f}".rstrip("0").rstrip(".")
    return number + "%"


def read_settings() -> dict:
    try:
        value = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def register_app_fonts():
    """Register fonts for this process only, including the native Tk menus."""
    if os.name != "nt":
        return
    gdi=ctypes.WinDLL("gdi32",use_last_error=True)
    gdi.AddFontResourceExW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint,ctypes.c_void_p]
    gdi.AddFontResourceExW.restype=ctypes.c_int
    for path in (FONT_REGULAR,FONT_SEMIBOLD):
        if path.is_file():
            gdi.AddFontResourceExW(str(path),0x10,None)


def monitor_work_area(x, y, width, height, fallback):
    """Get the nearest monitor's usable rectangle, excluding its taskbar."""
    if os.name != "nt":
        return fallback

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    user = ctypes.WinDLL("user32", use_last_error=True)
    user.MonitorFromRect.argtypes = [ctypes.POINTER(wintypes.RECT), wintypes.DWORD]
    user.MonitorFromRect.restype = wintypes.HANDLE
    user.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    user.GetMonitorInfoW.restype = wintypes.BOOL
    bounds = wintypes.RECT(x, y, x + width, y + height)
    monitor = user.MonitorFromRect(ctypes.byref(bounds), 2)  # nearest monitor
    info = MonitorInfo()
    info.cbSize = ctypes.sizeof(info)
    if monitor and user.GetMonitorInfoW(monitor, ctypes.byref(info)):
        area = info.rcWork
        return area.left, area.top, area.right, area.bottom
    return fallback


def move_window(root, x, y):
    if os.name == "nt":
        # Native absolute coordinates also work on monitors left/above primary.
        # Tk's negative geometry coordinates instead mean distance from an edge.
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user.GetAncestor.restype = wintypes.HWND
        user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user.SetWindowPos.restype = wintypes.BOOL
        hwnd = user.GetAncestor(root.winfo_id(), 2)
        if not user.SetWindowPos(hwnd, None, x, y, 0, 0, 0x0015):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        root.geometry(f"{x:+d}{y:+d}")


class QuotaWorker:
    """One background worker; refresh clicks cannot create concurrent requests."""
    def __init__(self, events: queue.Queue):
        self.events = events
        self.refresh = threading.Event()
        self.stopping = threading.Event()
        self.client = None
        self.client_lock = threading.Lock()
        self.thread = threading.Thread(target=self.run, name="quota-reader", daemon=True)

    def start(self):
        self.thread.start()

    def request_refresh(self):
        self.refresh.set()

    def run(self):
        from quota import parse_limits
        while not self.stopping.is_set():
            self.refresh.clear()
            self.events.put(("loading", None))
            client = None
            try:
                with self.client_lock:
                    if self.stopping.is_set():
                        break
                    client = RpcClient(discover_codex(), timeout=25)
                    self.client = client
                client.initialize()
                snapshot = parse_limits(client.request("account/rateLimits/read"))
                self.events.put(("snapshot", snapshot))
            except (QuotaError, OSError, ValueError):
                if not self.stopping.is_set():
                    self.events.put(("error", "连接未成功，请检查 Codex 登录和网络"))
                    logging.warning("Quota refresh failed; retaining timestamped previous reading")
            finally:
                # Close is serialized with shutdown, including the in-flight request.
                with self.client_lock:
                    if self.client is client and client is not None:
                        client.close()
                        self.client = None
            deadline = time.monotonic() + REFRESH_SECONDS
            while not self.stopping.is_set() and time.monotonic() < deadline:
                if self.refresh.wait(timeout=min(0.5, max(0.01, deadline-time.monotonic()))):
                    break

    def close(self):
        self.stopping.set()
        self.refresh.set()
        with self.client_lock:
            if self.client is not None:
                self.client.close()
                self.client = None
        self.thread.join(timeout=2)


class PetWindow:
    def __init__(self, root: tk.Tk, start_worker=True, persistent=True):
        self.root = root
        self.snapshot: Snapshot | None = None
        self.error = ""
        self.loading = start_worker
        self.expanded = False
        self.closed = False
        self.persistent = persistent
        self.demo = False
        self.dragging = False
        self.drag_origin = None
        self.hovering = False
        self.events = queue.Queue()
        self.worker = QuotaWorker(self.events) if start_worker else None
        self.settings = read_settings()
        self.appearance = tk.StringVar(value=self.settings.get("appearance", "graphite"))
        self.always_top = tk.BooleanVar(value=self.settings.get("topmost", True))
        self.palette = self.get_palette(self.appearance.get())
        self.fonts = {}
        self.photo = None
        self.canvas_image = None
        self.presenter = None
        self.last_layout = None
        self.last_status_write = 0.0
        self.attempted_resets: set[int] = set()
        self.placement = None
        self.root.title(TITLE)
        self.root.overrideredirect(True)
        self.root.configure(bg=TRANSPARENT)
        self.root.attributes("-topmost", self.always_top.get())
        if os.name == "nt":
            self.root.attributes("-toolwindow", True)
        self.canvas = tk.Canvas(root, width=WIDTH, height=self.height, bg=TRANSPARENT, highlightthickness=0)
        self.canvas.pack()
        self.root.bind("<Escape>", lambda _:self.close())
        self.root.bind("<F5>", lambda _:self.refresh())
        self.root.bind("<Return>", lambda _:self.toggle_details())
        self.canvas.bind("<ButtonPress-1>", self.pointer_down)
        self.canvas.bind("<B1-Motion>", self.pointer_move)
        self.canvas.bind("<ButtonRelease-1>", self.pointer_up)
        self.canvas.bind("<Button-3>", self.show_menu)
        self.canvas.bind("<Enter>", lambda _:self.set_hover(True))
        self.canvas.bind("<Leave>", lambda _:self.set_hover(False))
        self.menu = tk.Menu(root, tearoff=False, font=("MiSans", 12))
        self.menu.add_command(label="刷新额度   F5", command=self.refresh)
        self.menu.add_command(label="展开 / 收起详情", command=self.toggle_details)
        self.menu.add_checkbutton(label="始终置顶", variable=self.always_top, command=self.change_topmost)
        self.appearance_menu = tk.Menu(self.menu,tearoff=False,font=("MiSans",12))
        self.appearance_menu.add_radiobutton(label="石墨深色",variable=self.appearance,value="graphite",command=self.change_appearance)
        self.appearance_menu.add_radiobutton(label="雾白浅色",variable=self.appearance,value="mist",command=self.change_appearance)
        self.menu.add_cascade(label="外观",menu=self.appearance_menu)
        self.menu.add_command(label="字体：MiSans",state="disabled")
        self.menu.add_separator()
        self.menu.add_command(label="退出   Esc", command=self.close)
        self.anchor_x, self.anchor_y = self.default_position()
        if os.name == "nt":
            self.presenter = LayeredPresenter(self.root)
        self.render()
        if self.worker:
            self.worker.start()
        self.poll_id = self.root.after(100, self.poll)

    @staticmethod
    def get_palette(appearance="graphite"):
        if appearance == "mist":
            return {"panel":"#FAFCFF","hover":"#F1F5FA","pressed":"#E8EEF5","border":"#D8E1EC","ink":"#243248","muted":"#66778C","quiet":"#7B8BA0","track":"#E0E8F0","energy":"#238D83","low":"#A76B20","empty":"#C55263"}
        return {"panel":"#18212D","hover":"#202C3C","pressed":"#26354A","border":"#334052","ink":"#F2F6FC","muted":"#A3B2C6","quiet":"#8799B1","track":"#344255","energy":"#79DBCB","low":"#F2B76F","empty":"#F197A4"}

    @property
    def base_pill_y(self):
        return 22

    @property
    def base_height(self):
        return self.base_pill_y + CARD_HEIGHT + 8

    @property
    def content_offset(self):
        return self.placement.content_offset if self.expanded and self.placement else 0

    @property
    def pill_y(self):
        return self.base_pill_y + self.content_offset

    @property
    def height(self):
        return self.base_height + (DETAILS_HEIGHT if self.expanded else 0)

    def work_area(self, x, y):
        fallback = (0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight())
        return monitor_work_area(x, y, WIDTH, self.base_height, fallback)

    def default_position(self):
        screen_w, screen_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x = self.settings.get("x", max(8,screen_w-WIDTH-220))
        y = self.settings.get("y", max(8,screen_h-self.height-80))
        if not isinstance(x,int):
            x = max(8,screen_w-WIDTH-220)
        if not isinstance(y,int):
            y = max(8,screen_h-self.height-80)
        placement = place_widget(x,y,WIDTH,self.base_height,False,self.work_area(x,y))
        return placement.anchor

    def font(self, size, numeric=False):
        key=(size,numeric)
        if key not in self.fonts:
            path = FONT_SEMIBOLD if numeric else FONT_REGULAR
            try:
                self.fonts[key] = ImageFont.truetype(str(path), size*3)
            except OSError:
                fallback=Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc"
                try:
                    self.fonts[key] = ImageFont.truetype(str(fallback),size*3)
                except OSError:
                    self.fonts[key] = ImageFont.load_default(size=size*3)
        return self.fonts[key]

    def window_color(self, window, now):
        if window_text(window,self.snapshot,now)=="—":
            return self.palette["quiet"]
        return self.palette["empty" if window.remaining<=0 else "low" if window.remaining<=25 else "energy"]

    def render_image(self, now=None) -> Image.Image:
        now = time.time() if now is None else now
        image = Image.new("RGBA", (WIDTH*3,self.height*3), (0,0,0,0))
        draw = ImageDraw.Draw(image)
        p = self.palette

        def rect(box, fill, radius=0):
            draw.rounded_rectangle(tuple(int(n*3) for n in box), radius=radius*3, fill=fill)

        def text(position, value, fill=None, size=12, anchor=None, numeric=False):
            draw.text((position[0]*3,position[1]*3), value, font=self.font(size,numeric), fill=fill or p["ink"], anchor=anchor)

        def surface(box, fill, radius=CARD_RADIUS):
            rect(box,p["border"],radius)
            rect((box[0]+0.7,box[1]+0.7,box[2]-0.7,box[3]-0.7),fill,radius-0.7)

        y = self.pill_y
        short = self.snapshot.short if self.snapshot else None
        week = self.snapshot.week if self.snapshot else None
        pressed=self.drag_origin is not None and not self.dragging
        fill=p["pressed"] if pressed else p["hover"] if self.hovering else p["panel"]
        for index, (label, window) in enumerate((("5小时",short),("本周",week))):
            left=8+index*(CARD_WIDTH+6)
            surface((left,y,left+CARD_WIDTH,y+CARD_HEIGHT),fill)
            text((left+12,y+18),label,p["muted"],LABEL_SIZE,"lm")
            value = window_text(window,self.snapshot,now)
            text((left+CARD_WIDTH-12,y+18),value,p["ink"],VALUE_SIZE,"rm",numeric=True)
            # Separate, directly labeled tracks: no status dot or tiny battery glyph.
            track_left=left+12
            track_width=CARD_WIDTH-24
            rect((track_left,y+35,track_left+track_width,y+38),p["track"],1)
            if value != "—" and window.remaining > 0:
                rect((track_left,y+35,track_left+track_width*window.remaining/100,y+38),self.window_color(window,now),1)

        if self.expanded:
            dy = y-124-7 if self.content_offset else y+CARD_HEIGHT+7
            surface((8,dy,WIDTH-8,dy+124),p["panel"],DETAIL_RADIUS)
            text((22,dy+16),"额度详情",p["ink"],14,"lm")
            text((22,dy+41),"5小时重置",p["muted"],12,"lm")
            text((WIDTH-22,dy+41),local_time(short.resets_at if short else None),p["ink"],12,"rm")
            text((22,dy+62),"本周重置",p["muted"],12,"lm")
            text((WIDTH-22,dy+62),local_time(week.resets_at if week else None),p["ink"],12,"rm")
            if self.demo:
                status = "演示数据 · 非真实额度"
            elif self.loading:
                status = "正在读取额度…"
            elif self.error:
                status = "更新失败 · " + (local_time(self.snapshot.captured_at,"%H:%M:%S") if self.snapshot else "暂无数据")
            elif self.snapshot:
                age = now-self.snapshot.captured_at
                passed_reset = any(w and w.expired(now) for w in (short,week))
                status = "等待确认重置" if passed_reset else "数据已过期" if age > STALE_SECONDS else "更新于 " + local_time(self.snapshot.captured_at,"%H:%M:%S")
            else:
                status = "暂无数据"
            text((22,dy+88),status,p["muted"],11,"lm")
            text((22,dy+107),"每90秒刷新 · 右键设置",p["quiet"],11,"lm")
        elif self.error or self.loading or (self.snapshot and now-self.snapshot.captured_at > STALE_SECONDS):
            # A visible status prevents old cached numbers being mistaken for fresh data.
            if self.loading:
                status = "刷新中…"
            elif self.snapshot and now-self.snapshot.captured_at > STALE_SECONDS:
                status = "数据过期 · 点开查看"
            else:
                status = "连接未成功 · 点开查看"
            # Keep status inside the existing opaque pill, not on the transparent desktop.
            rect((WIDTH//2-72,y-20,WIDTH//2+72,y-2),p["panel"],7)
            text((WIDTH//2,y-11),status,p["muted"],11,"mm")
        # Preserve fractional alpha around curves for the native compositor.
        return image.resize((WIDTH,self.height),Image.Resampling.LANCZOS)

    def render(self):
        previous = self.placement
        if not self.dragging:
            area = self.work_area(self.anchor_x,self.anchor_y)
            collapsed = place_widget(self.anchor_x,self.anchor_y,WIDTH,self.base_height,False,area)
            self.anchor_x, self.anchor_y = collapsed.anchor
            self.placement = place_widget(self.anchor_x,self.anchor_y,WIDTH,self.base_height,
                                          self.expanded,area)
        self.canvas.config(width=WIDTH,height=self.height)
        frame = self.render_image()
        if self.placement != previous:
            # Queue Tk's final geometry without flushing an intermediate resize.
            self.root.geometry(f"{WIDTH}x{self.height}+{self.placement.x}+{self.placement.y}")
        if self.presenter:
            self.presenter.present(frame,self.anchor_x if self.dragging else self.placement.x,
                                   self.anchor_y-self.content_offset if self.dragging else self.placement.y)
        else:
            self.photo = ImageTk.PhotoImage(frame,master=self.root)
            if self.canvas_image is None:
                self.canvas_image = self.canvas.create_image(0,0,image=self.photo,anchor="nw")
            else:
                self.canvas.itemconfigure(self.canvas_image,image=self.photo)

    def set_hover(self, value):
        if self.hovering == value:
            return
        self.hovering = value
        self.render()

    def pointer_down(self,event):
        self.drag_origin = (event.x_root,event.y_root,self.placement.x,
                            self.placement.y,event.x,event.y)
        self.dragging = False
        self.render()

    def pointer_move(self,event):
        if self.drag_origin:
            dx,dy = event.x_root-self.drag_origin[0],event.y_root-self.drag_origin[1]
            if abs(dx)+abs(dy)>5:
                was_dragging=self.dragging
                self.dragging = True
                x=self.drag_origin[2]+dx
                y=self.drag_origin[3]+dy
                self.anchor_x,self.anchor_y=x,y+self.content_offset
                move_window(self.root,x,y)
                if not was_dragging:
                    self.render()

    def pointer_up(self,event):
        origin = self.drag_origin
        self.drag_origin = None
        if self.dragging:
            self.dragging = False
            self.render()
            self.save_settings()
            return
        elif origin:
            x,y = origin[4:6]
            if self.pill_y-18<=y<=self.pill_y+CARD_HEIGHT+8:
                self.toggle_details()
                return
        if not self.closed:
            self.render()

    def toggle_details(self):
        self.expanded = not self.expanded
        self.render()

    def show_menu(self,event):
        try:
            self.menu.tk_popup(event.x_root,event.y_root)
        finally:
            self.menu.grab_release()

    def change_topmost(self):
        self.root.attributes("-topmost",self.always_top.get())
        self.save_settings()

    def change_appearance(self):
        self.palette=self.get_palette(self.appearance.get())
        self.render()
        self.save_settings()

    def refresh(self):
        if self.worker and not self.loading:
            self.worker.request_refresh()

    def save_settings(self):
        if not self.persistent:
            return
        try:
            atomic_json(SETTINGS_PATH,{"x":self.anchor_x,"y":self.anchor_y,"topmost":self.always_top.get(),"appearance":self.appearance.get()})
        except OSError:
            logging.warning("Could not save window position")

    def diagnostics(self):
        now=time.time()
        return {"running":True,"pid":os.getpid(),"updated_at":now,"loading":self.loading,"error":bool(self.error),"demo":self.demo,"mood":mood(self.snapshot,now),"short":window_text(self.snapshot.short if self.snapshot else None,self.snapshot,now),"week":window_text(self.snapshot.week if self.snapshot else None,self.snapshot,now),"style":"compact-v4","appearance":self.appearance.get(),"font":self.font(LABEL_SIZE).getname()[0],"label_size":LABEL_SIZE,"value_size":VALUE_SIZE,"size":[WIDTH,self.height],"layout_version":"auto-placement-v2","renderer":"layered-rgba" if self.presenter else "tk-rgba","corner_radius":[CARD_RADIUS,DETAIL_RADIUS],"expanded":self.expanded,"direction":self.placement.direction,"anchor":[self.anchor_x,self.anchor_y],"origin":[self.placement.x,self.placement.y],"work_area":self.placement.work_area,"snapshot":self.snapshot.to_dict() if self.snapshot else None}

    def poll(self):
        if self.closed:
            return
        stop_path=RUNTIME/"stop.request"
        if self.persistent and stop_path.exists():
            stop_path.unlink(missing_ok=True)
            self.close()
            return
        changed=False
        while True:
            try:
                event,value = self.events.get_nowait()
            except queue.Empty:
                break
            if event=="loading":
                self.loading=True
            elif event=="snapshot":
                self.snapshot=value
                self.loading=False
                self.error=""
            elif event=="error":
                self.error=value
                self.loading=False
            changed=True
        now=time.time()
        # At a known reset, request a fresh reading; never infer a 100% refill.
        if self.snapshot and not self.loading:
            for window in (self.snapshot.short,self.snapshot.week):
                if window and window.expired(now) and window.resets_at not in self.attempted_resets:
                    self.attempted_resets.add(window.resets_at)
                    self.refresh()
        layout=(mood(self.snapshot,now),self.loading,bool(self.error),int(now)//5)
        if changed or layout!=self.last_layout:
            self.last_layout=layout
            self.render()
        if self.persistent and (changed or now-self.last_status_write>=10):
            try:
                atomic_json(RUNTIME/"status.json",self.diagnostics())
                self.last_status_write=now
            except OSError:
                pass
        self.poll_id=self.root.after(200,self.poll)

    def close(self):
        if self.closed:
            return
        self.closed=True
        self.root.after_cancel(self.poll_id)
        self.save_settings()
        if self.worker:
            self.worker.close()
        if self.presenter:
            self.presenter.close()
        if self.persistent:
            try:
                atomic_json(RUNTIME/"status.json",{"running":False,"pid":os.getpid(),"stopped_at":time.time()})
            except OSError:
                pass
        self.root.destroy()


def acquire_instance():
    if os.name != "nt":
        return None
    kernel=ctypes.WinDLL("kernel32",use_last_error=True)
    kernel.CreateMutexW.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_wchar_p]
    kernel.CreateMutexW.restype=ctypes.c_void_p
    handle=kernel.CreateMutexW(None,False,"Local\\XiaohengPetEnergy")
    if not handle:
        raise OSError("Could not create instance mutex")
    if ctypes.get_last_error()==183:
        kernel.CloseHandle.argtypes=[ctypes.c_void_p]
        kernel.CloseHandle(handle)
        return False
    return handle


def main():
    parser=argparse.ArgumentParser(description=TITLE)
    parser.add_argument("--demo",type=int,help="独立演示值0..100，不连接额度服务")
    parser.add_argument("--self-check",action="store_true",help="检查界面并保存预览，不启动常驻组件")
    args=parser.parse_args()
    RUNTIME.mkdir(exist_ok=True)
    logging.basicConfig(filename=RUNTIME/"app.log",level=logging.WARNING,format="%(asctime)s %(levelname)s %(message)s",encoding="utf-8")
    handle=acquire_instance() if not args.self_check else None
    if handle is False:
        return
    try:
        if os.name=="nt":
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except (AttributeError,OSError):
                pass
        register_app_fonts()
        root=tk.Tk()
        root.withdraw()
        window=PetWindow(root,start_worker=args.demo is None and not args.self_check,persistent=not args.self_check and args.demo is None)
        if args.demo is not None:
            value=max(0,min(100,args.demo))
            now=time.time()
            window.snapshot=Snapshot(Window(value,300,int(now+3600)),Window(value,10080,int(now+86400)),now)
            window.demo=True
            window.expanded=True
            window.render()
        if args.self_check:
            from types import SimpleNamespace
            now=time.time()
            window.snapshot=Snapshot(Window(57,300,int(now+3600)),Window(67,10080,int(now+86400)),now)
            window.render()
            root.update_idletasks()
            assert window.canvas.winfo_reqwidth()==WIDTH
            window.toggle_details()
            root.update_idletasks()
            assert window.expanded and window.canvas.winfo_reqheight()==window.height
            window.render()
            root.update_idletasks()
            assert window.height==208
            assert "MiSans" in window.font(LABEL_SIZE).getname()[0]
            assert "MiSans" in window.font(VALUE_SIZE,True).getname()[0]
            window.expanded=False
            window.snapshot=Snapshot(Window(22,300,int(now+3600)),Window(62,10080,int(now+86400)),now)
            assert window.window_color(window.snapshot.short,now)==window.palette["low"]
            assert window.window_color(window.snapshot.week,now)==window.palette["energy"]
            for appearance in ("graphite","mist"):
                window.appearance.set(appearance)
                window.change_appearance()
                assert window.height==76
                frame=window.render_image(now)
                assert frame.mode=="RGBA"
                corner_alpha=set(frame.getchannel("A").crop((8,window.pill_y,28,window.pill_y+20)).tobytes())
                assert 0 in corner_alpha and 255 in corner_alpha
                assert any(0<alpha<255 for alpha in corner_alpha)
                frame.save(RUNTIME/("preview-"+appearance+".png"))
                # MiSans labels and a maximum-width value fit at the larger sizes.
                for label in ("5小时","本周"):
                    label_width=window.font(LABEL_SIZE).getlength(label)/3
                    value_width=window.font(VALUE_SIZE,True).getlength("99.9%")/3
                    assert label_width+value_width+8<=CARD_WIDTH-24
                window.expanded=True
                window.render()
                window.render_image(now).save(RUNTIME/("preview-"+appearance+"-details.png"))
                window.expanded=False
            # Exercise the real Tk/native window, not only the placement helper.
            if window.presenter:
                window.presenter.opacity=0
                window.render()
            else:
                root.attributes("-alpha",0.0)
            root.deiconify()
            root.update()
            window.placement=None
            area=window.work_area(window.anchor_x,window.anchor_y)
            window.anchor_x=area[0]+20
            window.anchor_y=area[3]-window.base_height-8
            window.expanded=False
            window.render()
            root.update()
            anchor=(window.anchor_x,window.anchor_y)
            collapsed_origin=(root.winfo_x(),root.winfo_y())
            for _ in range(3):
                frames_before=window.presenter.frames if window.presenter else 0
                window.toggle_details()
                if window.presenter:
                    assert window.presenter.frames==frames_before+1
                    assert window.presenter.last_bounds==(window.placement.x,window.placement.y,WIDTH,window.height)
                    assert window.presenter.bounds()==window.presenter.last_bounds
                root.update()
                assert window.placement.direction=="up"
                assert (window.anchor_x,window.anchor_y)==anchor
                assert (root.winfo_x(),root.winfo_y())==(collapsed_origin[0],collapsed_origin[1]-DETAILS_HEIGHT), (collapsed_origin,root.geometry(),window.placement)
                assert root.winfo_y()+root.winfo_height()<=area[3]-8
                window.toggle_details()
                root.update()
                assert (root.winfo_x(),root.winfo_y())==collapsed_origin
            # A click on an upward-open badge closes it without moving it.
            window.toggle_details()
            root.update()
            click=SimpleNamespace(x=30,y=window.pill_y+20,
                                  x_root=window.placement.x+30,
                                  y_root=window.placement.y+window.pill_y+20)
            window.pointer_down(click)
            window.pointer_up(click)
            root.update()
            assert not window.expanded
            assert (root.winfo_x(),root.winfo_y())==collapsed_origin
            # Dragging the expanded popup upward can switch back to below.
            window.toggle_details()
            root.update()
            click.y=window.pill_y+20
            click.y_root=window.placement.y+click.y
            window.pointer_down(click)
            drag=SimpleNamespace(x_root=click.x_root+30,y_root=click.y_root-400)
            window.pointer_move(drag)
            window.pointer_up(drag)
            root.update()
            assert (window.anchor_x,window.anchor_y)==(anchor[0]+30,anchor[1]-400)
            assert window.placement.direction=="down"
            assert (root.winfo_x(),root.winfo_y())==(window.anchor_x,window.anchor_y)
            window.toggle_details()
            window.anchor_y=area[1]+20
            window.render()
            root.update()
            top_origin=(root.winfo_x(),root.winfo_y())
            window.toggle_details()
            root.update()
            assert window.placement.direction=="down"
            assert (root.winfo_x(),root.winfo_y())==top_origin
            window.toggle_details()
            # On a short display, fit the popup while retaining the return anchor.
            original_work_area=window.work_area
            window.work_area=lambda x,y:(0,0,800,300)
            window.anchor_x,window.anchor_y=100,90
            window.expanded=False
            window.render()
            window.toggle_details()
            assert (window.anchor_x,window.anchor_y)==(100,90)
            window.toggle_details()
            assert window.placement.anchor==(100,90)
            window.work_area=original_work_area
            if window.presenter:
                for topmost in (False,True):
                    window.always_top.set(topmost)
                    window.change_topmost()
                    window.render()
                    root.update()
                    assert window.presenter.bounds()==window.presenter.last_bounds
                # Repeated resized DIBs must release their GDI handles.
                kernel=ctypes.WinDLL("kernel32")
                kernel.GetCurrentProcess.restype=wintypes.HANDLE
                user=ctypes.WinDLL("user32")
                user.GetGuiResources.argtypes=[wintypes.HANDLE,wintypes.DWORD]
                user.GetGuiResources.restype=wintypes.DWORD
                process=kernel.GetCurrentProcess()
                handles_before=user.GetGuiResources(process,0)
                assert handles_before>0
                for _ in range(20):
                    window.toggle_details()
                    root.update()
                    assert window.presenter.bounds()==window.presenter.last_bounds
                assert user.GetGuiResources(process,0)<=handles_before+2
            window.close()
            print("UI self-check passed")
            return
        (RUNTIME/"stop.request").unlink(missing_ok=True)
        root.deiconify()
        root.mainloop()
    except Exception:
        logging.exception("Pet Energy could not run")
        raise
    finally:
        if handle:
            kernel=ctypes.WinDLL("kernel32",use_last_error=True)
            kernel.CloseHandle.argtypes=[ctypes.c_void_p]
            kernel.CloseHandle(handle)


if __name__=="__main__":
    main()
