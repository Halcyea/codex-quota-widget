"""Rebuild README images with the real renderer and fixed offline data."""
from datetime import datetime, timezone
from pathlib import Path
import sys
import tkinter as tk
from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from app import PetWindow, register_app_fonts
from placement import place_widget
from quota import Snapshot, Window

OUT = BASE / 'docs/images'
NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc).timestamp()

def text(image, xy, value, size=26, color='#BAC7D7', bold=False):
    name = 'MiSans-Semibold.ttf' if bold else 'MiSans-Regular.ttf'
    font = ImageFont.truetype(str(BASE / 'assets/fonts' / name), size)
    ImageDraw.Draw(image).text(xy, value, font=font, fill=color)

def widget(window, theme='graphite', expanded=False, up=False):
    window.appearance.set(theme)
    window.palette = window.get_palette(theme)
    window.expanded = expanded
    window.placement = place_widget(200, 600 if up else 100, 270, 76, expanded, (0, 0, 1000, 700))
    return window.render_image(NOW).resize((540, window.height * 2), Image.Resampling.LANCZOS)

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    register_app_fonts()
    root = tk.Tk()
    root.withdraw()
    window = PetWindow(root, start_worker=False, persistent=False)
    try:
        window.demo = True
        window.snapshot = Snapshot(Window(22, 300, int(NOW + 3600)), Window(62, 10080, int(NOW + 86400)), NOW)
        hero = Image.new('RGBA', (1440, 760), '#101923')
        ImageDraw.Draw(hero).rounded_rectangle((770, 100, 1360, 660), radius=32, fill='#172431', outline='#314252', width=2)
        text(hero, (80, 72), 'CODEX QUOTA WIDGET', 24, '#79DBCB', True)
        text(hero, (80, 176), '额度，一眼可见。', 68, '#F2F6FC', True)
        text(hero, (84, 282), '5小时与本周 · 独立桌面悬浮挂件', 28)
        for y, title, desc in [(408, '随手一看', '剩余百分比与独立进度条'), (478, '随处放置', '自由拖动，详情自动避让边界'), (548, '随心切换', '石墨深色 / 雾白浅色')]:
            text(hero, (84, y), title, 26, '#F2F6FC', True)
            text(hero, (240, y), desc, 24)
        hero.alpha_composite(widget(window, expanded=True), (795, 202))
        text(hero, (808, 140), '桌面实际渲染 · 离线示例', 23, '#8FA5BB')
        text(hero, (84, 684), 'Windows  /  Python  /  MiSans', 22, '#8FA5BB')
        text(hero, (1030, 684), '示例数据，非真实账户额度', 20, '#8FA5BB')
        hero.convert('RGB').save(OUT / 'overview.png', optimize=True)

        themes = Image.new('RGBA', (1440, 400), '#101923')
        ImageDraw.Draw(themes).rectangle((720, 0, 1440, 400), fill='#EDF2F8')
        text(themes, (84, 54), '石墨深色', 34, '#F2F6FC', True)
        text(themes, (804, 54), '雾白浅色', 34, '#243248', True)
        themes.alpha_composite(widget(window), (90, 130))
        themes.alpha_composite(widget(window, 'mist'), (810, 130))
        text(themes, (84, 320), '右键 → 外观 → 石墨深色', 24)
        text(themes, (804, 320), '右键 → 外观 → 雾白浅色', 24, '#66778C')
        themes.convert('RGB').save(OUT / 'themes.png', optimize=True)

        details = Image.new('RGBA', (1440, 690), '#EDF2F8')
        draw = ImageDraw.Draw(details)
        for left in (40, 750):
            draw.rounded_rectangle((left, 28, left + 650, 658), radius=24, fill='#E1E9F2')
        text(details, (90, 64), '空间足够：向下展开', 32, '#243248', True)
        text(details, (800, 64), '靠近底部：向上展开', 32, '#243248', True)
        details.alpha_composite(widget(window, 'mist', True), (90, 150))
        details.alpha_composite(widget(window, 'mist', True, True), (800, 150))
        for left in (90, 800):
            text(details, (left, 592), '点击双卡展开 / 收起 · 收起后保持原位', 24, '#66778C')
        details.convert('RGB').save(OUT / 'details.png', optimize=True)
        print('Saved 3 README illustrations with offline demonstration data.')
    finally:
        window.close()

if __name__ == '__main__':
    main()
