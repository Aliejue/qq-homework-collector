# -*- coding: utf-8 -*-
"""
作业收集器 —— 图形界面启动器
================================================================
双击 `start_gui.bat` 时会先运行这个文件。

它只干一件事：在你电脑上挑一个「带着 tkinter 图形库」的 Python，
然后用它打开 gui.py。

为什么需要它？
    一台电脑常常装了好几个 Python —— 微软商店的、Anaconda 的、
    msys64/MSYS2 的、某些软件自带裁掉图形的精简版……
    系统默认调用的那一个，不一定带 tkinter。
    直接用默认的去开会报 "No module named 'tkinter'"，
    表现为「双击了但什么都没发生」。

    所以这里挨个试，挑出真正能开窗口的那个，并把结果记进
    `_python路径.txt`，下次直接用它，不再重复探测。
================================================================
"""

from __future__ import annotations

import glob
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
GUI = BASE / "gui.py"
# 缓存文件名刻意用纯 ASCII：.bat 文件读取中文名会出现编码错位，
# 用 ASCII 名以后不管谁来引用都不会踩坑。
CACHE = BASE / "_python_path.txt"
FAIL_LOG = BASE / "_启动失败.log"
# 启动过程的临时输出写到系统临时目录 —— 成功时自动丢掉，
# 不往用户的文件夹里留垃圾；失败时才把内容摘抄到 _启动失败.log。
TMP_LOG = Path(tempfile.gettempdir()) / "作业收集器_启动输出.log"

APP = "作业收集器"
MIN_VER = (3, 9)

# 用子进程问一句：你是 Python 吗？版本多少？带 tkinter 吗？
PROBE = (
    "import sys\n"
    "try:\n"
    "    import tkinter\n"
    "except Exception as exc:\n"
    "    sys.exit('notkinter: %s' % exc)\n"
    "print('%d %d %s' % (sys.version_info[0], sys.version_info[1], tkinter.TkVersion))\n"
)

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ---------------------------------------------------------------- 小工具
def _dialog(text: str, title: str, error: bool = True) -> None:
    """弹一个原生对话框（不依赖 tkinter，避免「因为打不开窗口所以报不了错」）。"""
    if os.name == "nt":
        try:
            import ctypes

            flags = 0x10 if error else 0x40  # MB_ICONERROR / MB_ICONINFORMATION
            ctypes.windll.user32.MessageBoxW(None, text, title, flags)
            return
        except Exception:
            pass
    try:
        print(text)
    except Exception:
        pass


def _write_fail_log(text: str) -> None:
    try:
        FAIL_LOG.write_text(text, encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------- 找候选
def _candidates() -> list[Path]:
    """列出所有「可能能用」的 python.exe，按可能性从高到低。"""
    out: list[Path] = []
    seen: set[str] = set()

    def add(p) -> None:
        if not p:
            return
        try:
            q = Path(str(p).strip().strip('"'))
        except Exception:
            return
        name = q.name.lower()
        if name not in ("python.exe", "python3.exe", "python"):
            return
        try:
            if not q.is_file():
                return
        except OSError:
            return
        key = str(q).lower()
        if key in seen:
            return
        seen.add(key)
        out.append(q)

    # 1) 上次成功用过的那个 —— 最快
    try:
        if CACHE.is_file():
            txt = CACHE.read_text("utf-8", errors="replace").strip()
            if txt:
                add(txt.splitlines()[0])
    except Exception:
        pass

    # 2) 正在跑这个启动器的解释器
    add(sys.executable)

    # 3) PATH 里出现的每一个 python
    for d in os.environ.get("PATH", "").split(os.pathsep):
        d = d.strip().strip('"')
        if not d:
            continue
        for n in ("python.exe", "python3.exe"):
            add(os.path.join(d, n))

    # 4) 注册表 —— 官网安装版都会在这里登记，最靠谱
    try:
        import winreg

        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, r"Software\Python\PythonCore") as k:
                    i = 0
                    while True:
                        try:
                            ver = winreg.EnumKey(k, i)
                        except OSError:
                            break
                        i += 1
                        try:
                            with winreg.OpenKey(k, ver + r"\InstallPath") as k2:
                                add(os.path.join(winreg.QueryValue(k2, None),
                                                 "python.exe"))
                        except OSError:
                            continue
            except OSError:
                continue
    except Exception:
        pass

    # 5) 常见安装位置（含本机已知的 MSYS2 与自带 Python）
    var = os.path.expandvars
    pats = [
        r"C:\msys64\ucrt64\bin\python.exe",
        r"C:\msys64\mingw64\bin\python.exe",
        r"C:\msys64\clang64\bin\python.exe",
        var(r"%LOCALAPPDATA%\Programs\Python\Python3*\python.exe"),
        var(r"%LOCALAPPDATA%\Python\Python3*\python.exe"),
        var(r"%ProgramFiles%\Python3*\python.exe"),
        var(r"%SystemDrive%\Python3*\python.exe"),
        r"C:\Python3*\python.exe",
    ]
    for pat in pats:
        try:
            for p in sorted(glob.glob(pat), reverse=True):
                add(p)
        except Exception:
            continue
    return out


# ---------------------------------------------------------------- 逐个试探
def _probe(exe: Path) -> tuple[bool, str]:
    """问一个解释器：你能开窗吗？返回 (能否用, 说明)。"""
    try:
        r = subprocess.run(
            [str(exe), "-c", PROBE],
            capture_output=True,
            timeout=25,
            creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return False, "没响应（超时）"
    except Exception as e:
        return False, "无法启动（%s）" % e

    raw = (r.stderr or b"") + (r.stdout or b"")
    msg = raw.decode("utf-8", "replace").replace("\r", " ").strip()
    low = msg.lower()

    if r.returncode != 0:
        if "notkinter" in low or "no module named 'tkinter'" in low:
            return False, "没带 tkinter 图形库（精简版）"
        if "microsoft store" in low or "was not found" in low:
            return False, "是微软商店的占位程序，不是真的 Python"
        if "unable to create process" in low:
            return False, "指向的 Python 已经不存在了"
        first = msg.splitlines()[0] if msg else ""
        return False, (first[:110] or "退出码 %s" % r.returncode)

    out = (r.stdout or b"").decode("utf-8", "replace").strip()
    parts = out.split()
    if len(parts) < 3:
        return False, "输出不像 Python：%s" % out[:60]
    try:
        ver = (int(parts[0]), int(parts[1]))
        tkv = parts[2]
    except ValueError:
        return False, "输出不像 Python：%s" % out[:60]
    if ver < MIN_VER:
        return False, "版本太低（%d.%d，至少要 %d.%d）" % (
            ver[0], ver[1], MIN_VER[0], MIN_VER[1])
    return True, "%d.%d，Tk %s" % (ver[0], ver[1], tkv)


# ---------------------------------------------------------------- 主流程
def main() -> int:
    if not GUI.is_file():
        _dialog("找不到 gui.py：\n%s\n\n"
                "请确认「作业收集器」整个文件夹是完整的。" % GUI,
                APP + " · 启动失败")
        return 1

    tried: list[tuple[str, bool, str]] = []
    chosen: Path | None = None
    for exe in _candidates()[:14]:
        ok, note = _probe(exe)
        tried.append((str(exe), ok, note))
        if ok:
            chosen = exe
            break

    if chosen is None:
        lines = ["在你这台电脑上，没找到能开图形界面的 Python。", ""]
        if tried:
            lines.append("已经试过下面这些，都不行：")
            for p, _ok, note in tried[:8]:
                lines.append("  · %s" % p)
                lines.append("      → %s" % note)
            lines.append("")
        lines += [
            "怎么办（二选一）：",
            "1) 装一个完整的 Python 3.9 或更高版本：",
            "   https://www.python.org/downloads/",
            "   安装时务必勾上 “Add python.exe to PATH”，",
            "   并保持安装选项里默认勾选的 “tcl/tk and IDLE”。",
            "2) 或者先不用图形界面，直接双击 start.bat（命令行版）。",
        ]
        text = "\n".join(lines)
        detail = "\n".join("%s | %s | %s" % t for t in tried)
        _write_fail_log(text + "\n\n--- 探测明细 ---\n" + detail + "\n")
        _dialog(text, APP + " · 启动失败")
        return 1

    # 记住它，下次直接用这个，不再逐个试（启动更快）
    try:
        CACHE.write_text(str(chosen) + "\n", encoding="utf-8")
    except Exception:
        pass

    # 有 pythonw.exe 就用它 —— 不会多弹一个黑窗口
    runner = chosen.with_name("pythonw.exe")
    if not runner.is_file():
        runner = chosen

    # 启动 gui.py；把它的输出先写进临时日志，
    # 万一一启动就崩，我们能拿到原因弹给用户看。
    h = None
    try:
        h = open(TMP_LOG, "w", encoding="utf-8", errors="replace")
        sink = h
    except Exception:
        sink = subprocess.DEVNULL

    try:
        proc = subprocess.Popen(
            [str(runner), str(GUI)],
            cwd=str(BASE),
            stdin=subprocess.DEVNULL,
            stdout=sink,
            stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception as e:
        if h is not None:
            h.close()
        _dialog("启动失败：%s" % e, APP + " · 启动失败")
        return 1

    time.sleep(2.5)
    rc = proc.poll()
    if h is not None:
        try:
            h.close()
        except Exception:
            pass

    if rc is None:
        # 跑起来了 —— 清掉临时日志
        try:
            if TMP_LOG.is_file() and TMP_LOG.stat().st_size == 0:
                TMP_LOG.unlink()
        except Exception:
            pass
        return 0

    # 起来就退了 —— 把原因捞出来
    out = ""
    try:
        out = TMP_LOG.read_text("utf-8", errors="replace").strip()
    except Exception:
        pass
    text = "图形界面一启动就退出了（退出码 %s）。" % rc
    if out:
        text += "\n\n错误详情：\n" + out[-1000:]
    text += "\n\n详情已保存到：\n%s" % FAIL_LOG
    _write_fail_log(text + "\n\n用的解释器：%s\n" % runner)
    _dialog(text, APP + " · 启动失败")
    try:
        TMP_LOG.unlink()
    except Exception:
        pass
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
