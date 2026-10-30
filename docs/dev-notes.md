# 开发与打包笔记

面向要改这个项目的人（也就是未来的我）。环境相关的坑都在这里，省得重踩。

## 目录结构

```
app/                 程序本体
  app.py             装配层：配置 / 存储 / 监听 / 热键 / 托盘 / 消息泵 / 粘贴 / 退出
  config.py          JSON 配置，支持 portable.flag 便携模式
  db.py              SQLite + FTS5(trigram)，去重、置顶、容量上限、彻底清空
  watcher.py         剪贴板序列号轮询线程
  winclip.py         ctypes 剪贴板读写（文本 / CF_HTML / CF_DIB / CF_PNG / CF_HDROP）
  images.py          DIB ↔ Pillow 转换、WebP 压缩、缩略图
  paste.py           把选中内容写回剪贴板，可选自动 Ctrl+V 到目标窗口
  hotkey.py          RegisterHotKey + 线程消息循环
  tray.py            pystray 托盘图标与菜单
  ui/                主窗口（Canvas 虚拟滚动）、设置窗口、主题
tools/               自测与调试脚本，不参与打包
  selftest.py        纯逻辑自测（存储 / 搜索 / 图片转换 / 剪贴板写读），20 项
  e2e.py             端到端（真实剪贴板、热键、托盘），14 项
  paste_test.py      自动粘贴链路，3 项
  purge_test.py      彻底清除功能，9 项
  preview.py         灌样例数据后开窗口，人工看排版
  shot.py            PrintWindow 离屏截图（含 find_window）
  debug1.py          环境自检：进程会话 / WinSta0 / 热键 / 托盘
docs/                截图与本文档
run.py               源码入口
build.py             PyInstaller 打包脚本
```

## 本机环境

- Python 3.13.0：`D:\Python\Python313\python.exe`，项目内 `.venv`。
- PowerShell 是 5.1：不支持 `&&`、`??`、三元。中文输出要先设 `$env:PYTHONIOENCODING="utf-8"`，否则控制台乱码。

### 为什么留着一个 .venv（约 40 MB）

- 代码里没有任何地方按路径引用它，引用只出现在文档和命令里（`.venv\Scripts\python.exe`）。
- 但它目前是 **Pillow / pystray / PyInstaller 的唯一安装位置**：系统 Python 里这三个都没有，用 `D:\Python\Python313\python.exe` 直接 `import app.app` 会报 `ModuleNotFoundError: No module named 'PIL'`。
- 打包出来的 exe 自带这些库，**终端用户完全不需要 Python，也不需要这个 venv**；它只服务于"从源码跑 / 跑测试 / 重新打包"。
- `.venv` 已在 `.gitignore` 里，不进版本库。删了也能用 `python -m venv .venv` + `pip install -r requirements-dev.txt` 重建（重建后要重新补 `sitecustomize.py`，见下一节）。


## venv 里的 Tcl/Tk 找不到库

症状：`.venv` 的 python 直接 `tk.Tk()` 报
`_tkinter.TclError: Can't find a usable init.tcl in the following directories: ...`

原因：venv 里没有 tcl 目录，Tk 按 venv 前缀找库；真实库在 `D:\Python\Python313\tcl\`。
运行前设这两个环境变量即可（打包时也必须设，否则 hook 收不到 tcl/tk 数据）：

```powershell
$env:TCL_LIBRARY = "D:\Python\Python313\tcl\tcl8.6"
$env:TK_LIBRARY  = "D:\Python\Python313\tcl\tk8.6"
```

## 沙箱下 pip 装不动（`PermissionError: [Errno 13]`）

症状：`pip install` 报
`PermissionError: [Errno 13] Permission denied: '<tmp>\pip-unpack-xxxx\pillow-...whl'`。

原因：Windows 上 Python 把 `os.mkdir(path, 0o700)` 的 mode 转成 DACL，`tempfile.mkdtemp` 正好用 0o700，而受限环境下这种目录后续写入被拒。
绕过：`.venv\Lib\site-packages\sitecustomize.py` 里把 `tempfile.mkdtemp` 换成用 `os.mkdir(path, 0o777)` 自己生成目录。**这个文件只在本机生效，不属于项目代码，没有进 git。**

## 打包

```powershell
$env:TCL_LIBRARY = "D:\Python\Python313\tcl\tcl8.6"
$env:TK_LIBRARY  = "D:\Python\Python313\tcl\tk8.6"
$env:TMP = "$PWD\build\tmp"; $env:TEMP = $env:TMP
.\.venv\Scripts\python.exe build.py *> build\build.log
```

产物 `dist\ClipboardManager.exe`（约 19.5 MB 单文件）。

`build.py` 里两个容易被忽略的点：

- `HIDDEN = ["pystray._win32", "pystray._util.win32", "PIL._tkinter_finder"]` —— pystray 的后端是运行时动态导入，静态分析看不到，不加就会在托盘那步 ImportError。
- tcl/tk 数据靠构建时的 `TCL_LIBRARY` / `TK_LIBRARY` 让 hook 找到，spec 里的 `datas` 是空的。

## onefile exe 在受限环境里起不来（不是 bug）

PyInstaller 的 onefile 启动器会给解压目录设一段保护 DACL（`D:(A;;FA;;;S-1-5-21-...)`），某些受限环境下这种目录不允许再写入，于是：

```
LOADER: Failed to extract VCRUNTIME140.dll: failed to open target file!
LOADER: Failed to create parent directory structure.
```

用 `--debug bootloader --console` 构建能看到完整过程：它先 `created temporary directory: ...\_MEI00003b342` 成功，然后在解压那步失败——所以是"受保护 DACL 的目录里建不了子目录"，不是配置问题。

用户侧如果看到 `Error: Could not create temporary directory!`，说明卡得更早：连 `%TEMP%` 下那个 `_MEI*` 目录都没建成。常见原因是安全软件拦截、`%TEMP%` 被策略指到不可写路径、磁盘满、或进程在受限令牌下运行。

因此 `build.py` 现在同时产两种包：

| 产物 | 机制 | 何时用 |
| --- | --- | --- |
| `dist\ClipboardManager.exe` | onefile，启动解压到 `%TEMP%\_MEIxxxx` | 分发方便 |
| `dist\ClipboardManager-portable\` | onedir，直接跑 `_internal` | 任何 onefile 起不来的环境 |

命令行：`build.py`（两个都打）、`build.py --onefile`、`build.py --portable`。

**踩过的坑**：所有构建共用同一个命名互斥体 `Local\ClipboardManager.ControlPanel`，同一时间只能跑一个实例。上一个没退干净时，新启动的会弹"已经在运行了"，看起来像"一闪而过 / 启动失败"。排查前先清场：

```powershell
Get-Process | Where-Object { $_.ProcessName -match 'Clipboard|CMEx|CMWin|CMConsole' }
```

另外：用 PowerShell `& exe` 启动 GUI 程序不会等它结束，命令一返回、作业一退出，子进程可能被一起收掉，看起来也像"秒退"。想稳定观察，用 `cmd /c "exe"` 放后台作业，或者 `Start-Process`（注意它会挂住工具调用，别在前台用）。

## 在受限令牌里跑会出现的一串症状（认准这套组合）

如果 exe 是从聊天窗口的文件卡片、IDE 的「运行」按钮之类的地方启动的，进程会带着那个程序的受限令牌，于是同时出现：

1. `error.log` 第一行 `数据目录不可写：C:\Users\...\AppData\Roaming\ClipboardManager`，数据落到回退目录；
2. 托盘图标**哪里都不显示**（`^` 里也没有）——受限进程碰不到 explorer 的通知区域，Shell_NotifyIcon 被静默拦掉，但 pystray 那边 `icon.visible` 仍是 True，日志照样写"托盘图标已创建"；
3. 点条目不自动粘贴——`SendInput` 打不进别人家的窗口（UIPI）；
4. 单文件版报 `Could not create temporary directory!`。

四条一起出现就基本可以判定是启动方式的问题，不是程序 bug。程序现在会在启动时检测 ①，并弹窗提示"检测到受限运行环境，请在资源管理器里双击 exe"。

排查用的一行（看当前前台属于谁、目标能不能被置前）：

```powershell
.\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, '.'); from app import paste; print('fg', paste.foreground_window(), 'alive', paste.is_window(paste.foreground_window()))"
```

## 自测怎么跑

```powershell
$env:TCL_LIBRARY = "D:\Python\Python313\tcl\tcl8.6"
$env:TK_LIBRARY  = "D:\Python\Python313\tcl\tk8.6"
.\.venv\Scripts\python.exe tools\selftest.py        # 纯逻辑
.\.venv\Scripts\python.exe tools\e2e.py             # 真实剪贴板
.\.venv\Scripts\python.exe tools\paste_test.py      # 自动粘贴
.\.venv\Scripts\python.exe tools\purge_test.py      # 彻底清除
.\.venv\Scripts\python.exe tools\click_paste_test.py # 单击条目直接粘贴
```

注意：`e2e.py` 会临时占用剪贴板并在结束时还原；`paste_test.py` 需要能抢到前台窗口，抢不到时只验证写回，不算失败。

## 两个测试环境的限制

- **全局热键**：`RegisterHotKey` 能成功，但注入的按键在"前台被更高权限窗口占用"时收不到（UIPI 拦截）。`e2e.py` 抢不到前台就改用 `PostThreadMessageW(thread_id, WM_HOTKEY, ...)` 验证消息循环本身。
- **截图**：`PrintWindow(hwnd, dc, PW_RENDERFULLCONTENT)` 能抓到被遮挡的窗口；从后台任务启动的 Tk 窗口可能处于最小化（iconic），截图前要 `ShowWindow(hwnd, 9)`。高清屏下必须先 `SetProcessDpiAwareness(1)`，否则 `GetWindowRect` 返回虚拟化尺寸，截图会被裁一圈。

## 改代码时的两个坑

- 用 edit 工具替换时，`old_string` 若少写尾随换行，会把下一行的缩进吃掉，产生 `IndentationError`。改完先跑 `python -c "import ast; ast.parse(open(...,encoding='utf-8').read())"`。
- `ClipboardApp.run()` 内部就进 `mainloop()`，想在无交互场景测窗口，要自己搭：`MainWindow(app)` → `app.window = win` → `app._apply_window_scale()` → `win.root.mainloop()`，用 `root.after()` 收尾。
