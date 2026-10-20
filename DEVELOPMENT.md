# 额度挂件开发说明

面向 Windows 的独立 Codex 额度悬浮窗。运行环境为 Python 3.12+、Tk 与 Pillow。

## 模块

| 文件 | 职责 |
| --- | --- |
| `app.py` | 窗口、双卡绘制、菜单、拖动、刷新线程、设置和自检 |
| `quota.py` | 发现原生 Codex，通过 stdio App Server 查询并规范化额度 |
| `placement.py` | 不依赖 Tk 的工作区避让与上下展开算法 |
| `layered.py` | Windows RGBA 逐像素透明合成和 DIB / GDI 生命周期 |
| `start-pet.vbs` / `start-pet.cmd` | 隐藏控制台启动；旧文件名保持现有快捷方式兼容 |
| `stop-widget.vbs` | 写入停止请求，运行实例自行清理并退出 |
| `create-desktop-shortcut.ps1` | 创建当前项目的桌面快捷方式 |
| `tools/render_readme.py` | 用实际绘制代码和固定离线数据生成 README 图片 |
| `tests/` | 数据、刷新、退出、位置与画面提交检查 |

## 数据与线程

`QuotaWorker` 串行查询 `account/rateLimits/read`，将加载、快照或错误事件放入队列。Tk 主线程消费事件并绘制，后台线程不操作 Tk。每次查询关闭自己的子进程；退出时主动关闭进行中的查询并等待工作线程。

每 90 秒刷新；超过 180 秒、窗口缺失或重置时间已到时隐藏相应百分比。到达重置时间后重新请求数据，不推测满额。时间按 UTC+8 显示。`PET_ENERGY_CODEX` 可指定原生 `.exe` 路径；自动发现覆盖桌面运行时、npm 平台包与 PATH。

## 绘制与窗口位置

界面固定宽 270px；两张卡各 124×46px，收起高度 76px，展开高度 208px。只保留额度双卡布局。Pillow 以三倍分辨率绘制，缩小后保留连续 alpha；卡片圆角 16px，详情圆角 18px。

`LayeredPresenter` 把 RGBA 转换为预乘 BGRA，写入顶向下的 32 位 DIB，通过一次 `UpdateLayeredWindow` 提交最终位置、尺寸和完整帧。`render()` 不处理 Tk 空闲重绘，不在旧位置展示中间尺寸。Windows 下 Canvas 只承接输入。不要对这条合成路径使用 Tk `-transparentcolor` 或 `-alpha`，以免切换透明模式。

窗口通过 `MonitorFromRect` / `GetMonitorInfoW` 读取所在显示器的工作区，排除任务栏并预留 8px。`place_widget()` 优先向下展开，空间不足时向上展开 132px。狭小工作区会移动展开窗口以容纳内容；若整个工作区比窗口还小，当前没有缩放或滚动降级。

`PetWindow.anchor_x/anchor_y` 保存收起位置。向上展开时窗口上移、内容下移同样距离，卡片桌面位置不变。收起回到锚点，拖动结束重新判断展开方向。Windows 使用原生绝对坐标支持主屏左方、上方显示器的负坐标。

类名 `PetWindow`、启动文件名和单实例互斥量沿用旧标识，便于本地版本升级；项目没有角色显示开关、图片加载、动画或角色设置。旧设置中的 `robot` 字段被忽略，下次保存时自然移除。

## 本地状态

`.runtime/` 整体被 Git 忽略：

- `settings.json`：收起位置、置顶和外观，通过临时文件替换写入。
- `status.json`：运行状态、额度快照、窗口位置和绘制诊断；可能包含个人使用信息。
- `app.log`：应用错误与刷新失败记录。
- `stop.request`：退出请求。
- `preview-*.png`：窗口自检生成的离线预览。

单实例互斥量沿用 `Local\XiaohengPetEnergy`。自检绕过互斥量、禁用持久化与真实请求；演示使用明确标记的合成数据。

## 验证与图片

```powershell
python -m unittest discover -s tests -v
python app.py --self-check
python tools/render_readme.py
```

32 项检查：额度处理 15 项，后台刷新与退出 2 项，位置计算 9 项，像素与完整帧提交 6 项。窗口自检检查真实 Windows 几何、双主题、字体宽度、连续 alpha、上下展开、原位收起、拖动、置顶和反复变更尺寸后的 GDI 句柄释放。

README 图片直接复用 `PetWindow.render_image()`，使用固定时间、22% / 62% 合成快照及“演示数据”提示。不得将真实账户额度或本机状态文件用作公开演示素材。CI 在 Windows / Python 3.12 上执行单元测试与窗口自检。
