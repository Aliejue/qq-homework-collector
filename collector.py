# -*- coding: utf-8 -*-
"""
作业收集内核
================================================================
把 QQ 里收到的作业文件，自动完成：

    识别学号/姓名  ->  统一改名  ->  去重  ->  归档  ->  统计未交

这个文件被两个入口共同调用：
    run.py      手动扫描 / 后台监控 QQ 接收文件夹（推荐，零风险）
    qq_bot.py   配合 NapCat 做全自动 QQ 机器人（进阶）

纯标准库实现，不需要 pip 安装任何东西。
================================================================
"""

from __future__ import annotations

import csv
import glob
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------- 默认配置
DEFAULT_CONFIG = {
    "作业名称": "第一次作业",
    "归档目录": "作业归档",
    # 归档时按「第几次作业」分文件夹：第1次作业/、第2次作业/……
    # 设成 false 就退回老样子 —— 所有作业平铺在归档目录根下。
    "按次序分文件夹": True,
    "监控目录": [],
    "自动探测QQ接收目录": True,
    "文件后缀": [".docx", ".doc", ".wps", ".pdf", ".txt", ".md", ".rtf",
                 ".xlsx", ".xls", ".pptx", ".ppt", ".zip", ".rar", ".7z",
                 ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".py", ".c", ".cpp",
                 ".java", ".html", ".css", ".js"],
    "忽略文件名包含": ["~$", ".tmp", ".crdownload", ".part", ".download",
                       "desktop.ini", "thumbs.db", ".ds_store"],
    "名单文件": "学生名单.txt",
    "文件名分隔符": ["+"],
    "学号提取": r"\d{6,14}",
    "姓名提取": r"[\u4e00-\u9fa5]{2,5}",
    "可忽略的中文词": [
        "作业", "上交", "提交", "收作业", "打卡", "副本", "复制", "最终版", "完整版",
        "修改版", "新版", "旧版", "初稿", "定稿", "文档", "文字文稿", "我的", "新建",
        "第一次", "第1次", "第二次", "第2次", "第三次", "第3次", "第四次", "第4次",
        "实验报告", "实验", "练习", "习题", "答案", "课程", "设计", "报告", "论文",
        "汇总", "整理", "重命名", "最终", "已修改", "改后",
    ],
    "命名模板": "{作业名称}_{学号}_{姓名}{后缀}",
    "有名单时只认名单": True,
    "未知学号占位": "未知学号",
    "未知姓名占位": "未知姓名",
    "归档方式": "复制",
    "重复策略": "最新",
    "最小文件大小": 200,
    "稳定等待秒": 5,
    "轮询间隔秒": 5,
    "启动时扫描已有文件": True,
    "启动时搜索历史作业": True,
    "最大扫描深度": 3,
    "搜索范围": [],
    "机器人": {
        "监听地址": "127.0.0.1",
        "监听端口": 8765,
        "OneBot地址": "http://127.0.0.1:3000",
        "OneBot令牌": "",
        "只处理这些群": [],
        "只处理这些好友": [],
        "收到后回复": True,
        "回复模板": "已收到你的作业《{作业名称}》，已自动存档为：{文件名}",
    },
}

CONFIG_NAME = "config.json"
STATE_NAME = "_处理记录.json"
LOG_NAME = "_日志.log"
LOG_OLD_NAME = "_日志.旧.log"
LOG_MAX_BYTES = 512 * 1024           # 日志超过这个大小就归档换代，不让它无限长
DUP_DIR = "_重复文件"
UNKNOWN_DIR = "_未识别"
ORDER_UNKNOWN_DIR = "未标次序"      # 认不出「第几次」时的兜底文件夹
REPORT_CSV = "_收集情况.csv"
MISSING_CSV = "_未交名单.csv"

# 打包产物的固定前缀。带上它有三个好处：
#   1. 一眼能看出「这是程序打包出来的，不是同学的作业」
#   2. 清理时认得出来（见 scan_junk）
#   3. 万一你把打包文件夹拖到桌面/下载，扫描会整目录跳过，
#      不会把里面的作业再当「新交的」收一遍
PACK_PREFIX = "打包_"

WINDOWS_BAD_CHARS = r'\/:*?"<>|'


# ---------------------------------------------------------------- 基础工具
def base_dir() -> Path:
    """程序所在目录。"""
    return Path(__file__).resolve().parent


def setup_console() -> None:
    """
    让 Windows 控制台稳定显示中文。

    不装任何第三方库，只做两件事：
      1. 把控制台的输入/输出代码页切到 UTF-8
      2. 把 stdout / stderr 的编码统一成 UTF-8
    这样无论用 cmd、PowerShell 还是直接双击 .bat，中文都不会变成乱码。
    """
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def safe_filename(name: str) -> str:
    """去掉 Windows 文件名里的非法字符。"""
    for ch in WINDOWS_BAD_CHARS:
        name = name.replace(ch, "_")
    name = re.sub(r"\s+", " ", name).strip()
    # Windows 不允许文件名以点或空格结尾
    return name.strip(" .") or "未命名"


def human_size(num: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.0f}{unit}" if unit == "B" else f"{num:.1f}{unit}"
        num /= 1024.0
    return f"{num}B"


def ensure_config() -> Path:
    """config.json 不存在就生成一份默认的。"""
    p = base_dir() / CONFIG_NAME
    if not p.exists():
        p.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2),
                     encoding="utf-8")
        print(f"[初始化] 已生成默认配置文件：{p}")
    return p


def load_config() -> dict:
    p = ensure_config()
    raw = json.loads(p.read_text(encoding="utf-8"))
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(raw)
    cfg["机器人"] = {**DEFAULT_CONFIG["机器人"], **raw.get("机器人", {})}
    return cfg


def save_config(cfg: dict) -> None:
    p = base_dir() / CONFIG_NAME
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def archive_dir(cfg: dict) -> Path:
    """归档根目录，并保证子目录都存在。"""
    d = Path(cfg["归档目录"]).expanduser()
    if not d.is_absolute():
        d = base_dir() / d
    d.mkdir(parents=True, exist_ok=True)
    (d / DUP_DIR).mkdir(exist_ok=True)
    (d / UNKNOWN_DIR).mkdir(exist_ok=True)
    return d


# ------------------------------------------------- 归档结构（按「第几次」分文件夹）
def order_letter(cfg: dict, fields: dict | None = None) -> str:
    """
    这次作业是「第几次」—— 统一成 `第1次` 这种写法；认不出来返回空串。

    先看文件名字段里的次序（最可信），取不到就退回配置里的「作业名称」——
    课代表每换一次作业都会把作业名称改成「高等数学I第2次作业」，
    所以就算同学的文件名忘了写第几次，也知道该归到哪一次去。
    """
    order = str((fields or {}).get("次序") or "").strip()
    if not order or order == "第?次":
        order = pick_order(str(cfg.get("作业名称") or ""), cfg)
    return "" if order == "第?次" else order


def order_folder(cfg: dict, fields: dict | None = None) -> str:
    """
    这次作业该放进归档目录下的哪个子文件夹。

    分文件夹 = 同一份作业的所有人放一起：`第1次作业/`、`第2次作业/`……
    次序彻底认不出来时退到 `未标次序/`，绝不往归档根目录里乱丢。

    想让所有作业平铺在归档根目录（老行为），把 config.json 里的
    `按次序分文件夹` 设成 false 即可。
    """
    if not cfg.get("按次序分文件夹", True):
        return ""
    order = order_letter(cfg, fields)
    return f"{order}作业" if order else ORDER_UNKNOWN_DIR


def work_dirs(cfg: dict) -> list[Path]:
    """
    归档目录下「装着正式作业」的所有文件夹：根目录自己 + 每个次序子文件夹。

    根目录也留着，是为了兼容老版本平铺归档的文件，免得统计时把它们漏掉。
    `_未识别/`、`_重复文件/` 是待处理区，不算作业，排除在外。
    """
    ad = archive_dir(cfg)
    out = [ad]
    try:
        for p in sorted(ad.iterdir()):
            if p.is_dir() and not p.name.startswith("_") and not p.name.startswith("."):
                out.append(p)
    except OSError:
        pass
    return out


def reorganize(cfg: dict, quiet: bool = False) -> list[dict]:
    """
    把归档根目录下「直接躺着」的作业文件，归进对应的次序子文件夹。

    老版本的归档是平铺的；改成分文件夹之后，这个函数负责把历史遗留的
    文件搬进去，让整个归档结构统一。

    **只搬程序自己归档的作业**（判据：文件名里认得出本班学号，
    且在学生名单里），你手动丢进归档目录的资料一概不碰。

    幂等：已经归好位的文件不会重复处理；关掉分文件夹功能时它什么都不做。
    """
    if not cfg.get("按次序分文件夹", True):
        return []
    ad = archive_dir(cfg)
    exts = [e.lower() for e in cfg["文件后缀"]]
    roster = load_roster(cfg)
    moved: list[dict] = []

    try:
        entries = sorted(ad.iterdir())
    except OSError:
        return moved

    for p in entries:
        if not p.is_file() or p.name.startswith("_") or p.suffix.lower() not in exts:
            continue
        sid, _name = parse_archived_name(p.name, cfg)
        if not sid:
            continue                    # 认不出学号 -> 不是程序归档的，别动
        if roster and sid not in roster:
            continue                    # 名单外的人 -> 可能不是这次收的作业，别动

        sub = order_folder(cfg, {"次序": pick_order(p.stem, cfg)})
        if not sub:
            continue
        dst_dir = ad / sub
        try:
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst = unique_path(dst_dir / p.name)
            shutil.move(str(p), str(dst))
        except OSError as e:
            log(cfg, f"  ! 归档整理失败 {p.name}：{e}")
            continue
        moved.append({"文件": p.name, "原位置": str(p), "新位置": str(dst), "次序": sub})
        if not quiet:
            log(cfg, f"  归档整理：{p.name}  ->  {sub}/{dst.name}")

    return moved


def log(cfg: dict, msg: str) -> None:
    """
    同时打印到控制台并写入日志文件。

    日志是「只追加」的，正常收一学期也就几十 KB（实测：不处理文件时
    空跑监控完全不增长）。但为了不留一个能无限长大的文件，超过
    LOG_MAX_BYTES 就改名叫 `_日志.旧.log`，只保留上一代，新的从头写。
    """
    line = f"[{now_str()}] {msg}"
    print(line)
    p = archive_dir(cfg) / LOG_NAME
    try:
        if p.exists() and p.stat().st_size > LOG_MAX_BYTES:
            p.replace(p.with_name(LOG_OLD_NAME))     # 只留一代，覆盖更旧的
        with open(p, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_state(cfg: dict) -> dict:
    p = archive_dir(cfg) / STATE_NAME
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_state(cfg: dict, state: dict) -> None:
    p = archive_dir(cfg) / STATE_NAME
    p.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- 学生名单
def roster_path(cfg: dict) -> Path:
    p = Path(cfg["名单文件"]).expanduser()
    if not p.is_absolute():
        p = base_dir() / p
    return p


def load_roster(cfg: dict) -> dict:
    """
    读取学生名单，返回 {学号: 姓名}。

    支持每行： `20231234 张三` / `20231234,张三` / `20231234,张三,备注`
    也支持只有名字没有学号的行（学号用 名称 占位）。
    以 # 开头的行是注释。
    """
    p = roster_path(cfg)
    roster: dict[str, str] = {}
    if not p.exists():
        return roster

    sid_re = re.compile(cfg["学号提取"])
    for raw_line in p.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [x for x in re.split(r"[\s,，\t;；]+", line) if x]
        sid, name = "", ""
        for tok in parts:
            if not sid and sid_re.fullmatch(tok):
                sid = tok
            elif not name and re.fullmatch(r"[\u4e00-\u9fa5A-Za-z·]{1,10}", tok):
                name = tok
        if not sid and not name:
            continue
        if not sid:
            # 没有学号的行：用姓名做 key，避免覆盖
            sid = f"@{name}"
        roster[sid] = name
    return roster


def name_to_sid(roster: dict) -> dict:
    """姓名 -> 学号 的反查表。"""
    out = {}
    for sid, name in roster.items():
        if name and name not in out:
            out[name] = sid
    return out


# ---------------------------------------------------------------- 目录探测
# 判断一个目录「像不像收作业的地方」时，认这些文档后缀
DOC_EXTS = {".pdf", ".doc", ".docx", ".wps", ".txt", ".md",
            ".xls", ".xlsx", ".ppt", ".pptx",
            ".zip", ".rar", ".7z"}

# 递归搜索时要主动绕开的目录。
# QQ / 微信的缓存目录里动辄几万个缩略图和小图标，后缀还偏偏是 .jpg/.png
# （正好在「文件后缀」白名单里），不剪掉的话搜索会又慢又脏。
SKIP_DIR_NAMES = {
    # QQ NT
    "thumb", "thumbtemp", "pic", "video", "ptt", "emoji", "avatar",
    "log", "log-cache", "msf", "dataline", "flashfransfer", "ams", "mmkv",
    "onlinestatus", "privilegeicon", "qzone", "unitedconfig", "wmpf",
    "nt_temp", "temp", "tmp",
    # 微信
    "wxid_cache", "cache", "caches", "attachment", "sns", "favorite",
    "wechat files", "xwechat_files",
    # 系统 / 开发垃圾
    "appdata", "application data", "node_modules", "__pycache__",
    "system volume information", "$recycle.bin", "windows",
    "缓存", "垃圾", "临时",
}

# 这些目录名出现在「文档」里时，本身已经作为独立搜索根处理过了，
# 再跟着 Documents 走一遍纯属浪费时间。
SEARCH_ROOT_SKIP = {"tencent files", "tencentqq", "wechat files", "xwechat_files",
                    "tencent"}


def candidate_qq_dirs() -> list[Path]:
    """
    自动探测 QQ 的「文件接收目录」。

    QQ 版本很多，路径不统一，这里把常见的都扫一遍：

      · 新版 QQ（NT 版，现在的主流）：
            Documents\\Tencent Files\\<QQ号>\\nt_qq\\nt_data\\File
        这台机器上实测就是这一种（QQNT 9.x）。
      · 老版 QQ / TIM：
            Documents\\Tencent Files\\<QQ号>\\FileRecv

    注意：目录可能是**空的**。新装的 QQ、或者还没人给你发作业时它当然是空的，
    空不代表找错了 —— 所以这里**不按「有没有文件」过滤掉**（旧版曾经这么干，
    结果在没收到过作业的机器上什么都找不到）。
    最终以 QQ「设置 -> 文件管理」里显示的路径为准。
    """
    home = Path.home()
    appdata = home / "AppData"
    pats: list[Path] = []
    for docs in (home / "Documents", home / "OneDrive" / "Documents"):
        pats += [
            # —— 新版 QQ NT ——
            docs / "Tencent Files" / "*" / "nt_qq" / "nt_data" / "File",
            docs / "TencentQQ" / "*" / "nt_qq" / "nt_data" / "File",
            # —— 老版 QQ / TIM ——
            docs / "Tencent Files" / "*" / "FileRecv",
            docs / "TencentQQ" / "*" / "FileRecv",
            docs / "Tencent Files" / "FileRecv",
            docs / "TencentQQ" / "FileRecv",
            docs / "QQ" / "*" / "FileRecv",
        ]
    pats += [
        appdata / "Roaming" / "Tencent" / "QQ" / "*" / "FileRecv",
        appdata / "Roaming" / "Tencent" / "QQ" / "NT" / "User" / "*" / "file",
        appdata / "Local" / "Tencent" / "QQ" / "*" / "FileRecv",
    ]

    found: list[Path] = []
    for pat in pats:
        # 必须用 glob.glob 展开整条模式。
        # 不能用 Path.parent.glob(name)：父路径里的 `*` 根本不会被展开，
        # 所以凡是带通配符的模式全部失效 —— 这正是以前探测不到目录的原因。
        try:
            for s in glob.glob(str(pat)):
                p = Path(s)
                if p.is_dir() and p not in found:
                    found.append(p)
        except OSError:
            continue
    return sorted(found, key=_dir_rank)


def _dir_rank(p: Path) -> tuple:
    """
    给候选目录排序，越可能是「收作业的地方」排越前：
      0 = 里面有文档      —— 基本就是它了
      1 = 空目录          —— 新装的 QQ 还没收到过东西，也对
      2 = 只有一堆图片缓存 —— 多半是聊天图片目录，不该选
    """
    docs, total, newest = dir_activity(p)
    if docs:
        return (0, -docs, -newest)
    if total == 0:
        return (1, 0, 0.0)
    return (2, -total, -newest)


def pick_watch_candidates(cands: list[Path]) -> list[Path]:
    """从候选目录里挑出该监控的：有文档的全要；一个都没有时，先挂最可能的那个。"""
    picked = [p for p in cands if dir_activity(p)[0] > 0]
    if not picked and cands:
        picked = cands[:1]
    return picked


def _iter_files(root: Path, limit: int | None = None):
    """遍历目录下的文件（不进子目录）。"""
    n = 0
    try:
        for entry in os.scandir(root):
            if entry.is_file():
                yield Path(entry.path)
                n += 1
                if limit and n >= limit:
                    return
    except OSError:
        return


def walk_files(root: Path, max_depth: int = 3, skip_names=None, exclude=None):
    """
    递归遍历目录下的文件，顺便剪枝。

    - `max_depth`：往下钻几层。0 = 只看这一层。
    - 名为 Thumb / Pic / 缓存 之类的目录直接跳过（见 SKIP_DIR_NAMES）；
      隐藏目录（.git 这种）也跳过。
    - `exclude`：这些目录（及其子孙）一律不进去 —— 用来挡住程序自己的目录，
      否则会把 demo / 归档 / _重复文件 当成「新收到的作业」再收一遍。
    - 任何一层出错都当作「这层没有」，绝不因为一个坏目录中断整个搜索。
    """
    root = Path(root)
    skip = {s.lower() for s in (skip_names or SKIP_DIR_NAMES)}
    ex: list[Path] = []
    for e in (exclude or []):
        try:
            ex.append(Path(e).resolve())
        except OSError:
            pass
    base_depth = len(root.parts)
    try:
        for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
            here = Path(dirpath)
            level = len(here.parts) - base_depth
            kept = []
            for n in dirnames:
                # 打包产物（打包_xxx）整目录跳过：里面的作业是我们自己
                # 打包出去的副本，再收一遍就成死循环了。
                if n.lower() in skip or n.startswith(".") or n.startswith(PACK_PREFIX):
                    continue
                child = here / n
                if any(child == e or _inside(child, e) for e in ex):
                    continue
                kept.append(n)
            dirnames[:] = kept if level < max_depth else []
            for n in filenames:
                yield here / n
    except OSError:
        return


def _inside(p: Path, root: Path) -> bool:
    """p 是不是在 root 里面（含 root 自己）。"""
    try:
        Path(p).resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError):
        return False


def exclude_roots(cfg: dict) -> list[Path]:
    """
    扫描时要整块绕开的目录。

    程序自己的目录必须排除，否则「搜索历史作业」会把
    自己生成的 demo / 归档 / _重复文件 当成同学交上来的作业，来回打转。
    """
    out = [base_dir()]
    try:
        out.append(archive_dir(cfg))
    except OSError:
        pass
    return out


def dir_activity(p: Path) -> tuple[int, int, float]:
    """
    统计一个目录里的 (文档数, 文件总数, 最新修改时间)。只看这一层，不进子目录。

    用来回答两个问题：这个目录像不像收作业的地方？最近有没有动静？
    """
    docs = total = 0
    newest = 0.0
    for f in _iter_files(p):
        total += 1
        try:
            newest = max(newest, f.stat().st_mtime)
        except OSError:
            pass
        if f.suffix.lower() in DOC_EXTS:
            docs += 1
    return docs, total, newest


def resolve_watch_dirs(cfg: dict) -> list[Path]:
    """得出本次要监控的目录列表。"""
    dirs: list[Path] = []
    for item in cfg.get("监控目录", []):
        p = Path(item).expanduser()
        if not p.is_absolute():
            p = base_dir() / p
        if p.is_dir():
            dirs.append(p)
    if cfg.get("自动探测QQ接收目录"):
        for p in pick_watch_candidates(candidate_qq_dirs()):
            if p not in dirs:
                dirs.append(p)
    return dirs


# ---------------------------------------------------------------- 历史作业搜索
def wechat_dirs() -> list[Path]:
    """微信 / 企业微信的文件接收目录（有就用，没有就算了）。"""
    home = Path.home()
    pats = [
        home / "Documents" / "xwechat_files" / "*" / "msg" / "file",
        home / "Documents" / "WeChat Files" / "*" / "FileStorage" / "File",
        home / "Documents" / "WXWork" / "*" / "Cache" / "File",
    ]
    out: list[Path] = []
    for pat in pats:
        for s in glob.glob(str(pat)):
            p = Path(s)
            if p.is_dir() and p not in out:
                out.append(p)
    return out


def common_dirs() -> list[Path]:
    """
    用户主目录下那几个「什么文件都会往里堆」的通用文件夹。

    同学发来的作业最常落在这里（尤其是**下载**文件夹）——
    实测本机就是这样：QQ 的 nt_data\\File 一个文档都没有，
    作业全在 `下载` 里躺着。
    """
    home = Path.home()
    out: list[Path] = []
    for p in (home / "Downloads", home / "Desktop", home / "Documents",
              home / "OneDrive" / "Desktop", home / "OneDrive" / "Documents"):
        try:
            if p.is_dir():
                out.append(p.resolve())
        except OSError:
            pass
    return out


def is_mixed_dir(p: Path) -> bool:
    """
    这个目录是不是「混合目录」——即里面什么文件都有，只能用严格规则挑作业。

    QQ 的接收目录特判为**不是**混合目录：它是专门收文件的地方，
    按后缀收即可（认不出人的丢 _未识别/ 让你核对），不需要那么苛刻。
    """
    try:
        rp = Path(p).resolve()
    except OSError:
        return False
    for q in candidate_qq_dirs():
        try:
            if rp == q.resolve():
                return False
        except OSError:
            continue
    for c in common_dirs() + wechat_dirs():
        if rp == c or _inside(rp, c):
            return True
    return False


def search_roots(cfg: dict) -> list[Path]:
    """
    搜索历史作业时，从哪些地方开始找。

    默认把「同学发来的文件最可能落地的几个位置」全排上：
        下载文件夹 / 桌面 / 文档 / QQ 接收目录 / 微信接收目录

    实测：本机同学发来的作业就落在 `下载(Downloads)` 里，
    而不是 QQ 那个 nt_data\\File —— 那里全是聊天图片缓存。
    所以「只盯 QQ 目录」是收不到东西的，必须一起搜。

    想只搜特定位置，就在 config.json 里填「搜索范围」，
    一旦填了，就只用你填的这几个。
    """
    custom = [x for x in (cfg.get("搜索范围") or []) if x]
    if custom:
        cands = [Path(x).expanduser() for x in custom]
    else:
        cands = list(common_dirs())
        cands += candidate_qq_dirs()
        cands += wechat_dirs()

    out: list[Path] = []
    for p in cands:
        try:
            p = p.resolve()
        except OSError:
            continue
        if not p.is_dir() or p in out:
            continue
        # 绝不搜索程序自己所在的目录（会把归档、demo 之类的再收一遍）
        if _inside(p, base_dir()):
            continue
        out.append(p)
    return out


def looks_like_homework(path: Path, cfg: dict, roster: dict) -> dict | None:
    """
    「这像不像一份作业」的**严格**判据 —— 专给历史搜索用。

    光看后缀是不够的：下载文件夹里躺着课表、竞赛资料、安装包……
    所以要求文件名必须能认出**学号**；配了名单的话，学号还得**在名单里**。
    宁可少收几个让你手动捞，也不要往归档里塞一堆无关文件。
    """
    if not is_wanted(path, cfg):
        return None
    fields = extract_fields(path.stem, cfg, roster)
    sid = fields.get("学号") or ""
    if not sid:
        return None
    real_sids = [s for s in roster if not s.startswith("@")]
    if real_sids and sid not in roster:
        return None      # 认出了学号，但不是本班的人 -> 不是你要收的作业
    return fields


def search_homework(cfg: dict, roster: dict | None = None, state: dict | None = None,
                    quiet: bool = False, collect: bool = True) -> dict:
    """
    满世界找一遍「历史作业」——也就是**你打开程序之前**就已经收到、
    但还没归档的那些文件。

    做法：
      1. 把搜索根目录（下载 / 桌面 / 文档 / QQ / 微信）递归走一遍
      2. 用严格判据筛出「文件名里带本班学号」的文件
      3. 默认直接把它们收进归档目录（collect=False 时只报告、不动文件）

    返回：
        {
          "扫描目录": [...], "找到": [(路径, 字段), ...], "结果": [...],
          "目录计数": {目录: 命中数}, "总文件数": N,
        }
    """
    if roster is None:
        roster = load_roster(cfg)
    if state is None:
        state = load_state(cfg)

    roots = search_roots(cfg)
    depth = max(0, int(cfg.get("最大扫描深度", 3)))

    if not quiet:
        print("正在找你打开程序之前收到的作业……")
        print(f"  搜索位置（往下 {depth} 层）：")
        for r in roots:
            print(f"    - {r}")

    hits: list[tuple[Path, dict]] = []
    scanned = 0
    ex = exclude_roots(cfg)
    for r in roots:
        for p in walk_files(r, depth, exclude=ex):
            scanned += 1
            f = looks_like_homework(p, cfg, roster)
            if f:
                hits.append((p, f))

    results: list[dict] = []
    dir_count: dict[str, int] = {}
    for p, _f in hits:
        dir_count[str(p.parent)] = dir_count.get(str(p.parent), 0) + 1
        if not collect:
            continue
        key = str(p.resolve())
        try:
            st = p.stat()
        except OSError:
            continue
        old = state.get(key)
        if old and old.get("size") == st.st_size and abs(old.get("mtime", 0) - st.st_mtime) < 1:
            continue          # 早就收过了
        if not is_settled(p, cfg):
            continue          # 还在下载中，下一轮再说
        results.append(process_file(p, cfg, state, roster, source="历史搜索"))

    if collect and results:
        save_state(cfg, state)

    report = {
        "扫描位置": [str(r) for r in roots],
        "总文件数": scanned,
        "找到": hits,
        "结果": results,
        "目录计数": dir_count,
    }

    if not quiet:
        print()
        if not hits:
            print("没找到符合条件的作业文件。")
            if not roster:
                print("（提示：还没填「学生名单.txt」，无法判断文件是不是本班的，"
                      "建议先填名单再搜。）")
        else:
            print(f"找到 {len(hits)} 份像是作业的文件，分布在 {len(dir_count)} 个文件夹：")
            for d, n in sorted(dir_count.items(), key=lambda x: -x[1]):
                print(f"  {n:>3} 份  {d}")
            if collect:
                ok = sum(1 for r in results if r["状态"] == "已归档")
                dup = sum(1 for r in results if r["状态"] == "重复")
                print()
                print(f"本次新归档 {ok} 份，重复留档 {dup} 份"
                      f"（其余 {len(hits) - len(results)} 份之前已经收过）。")
    return report


# ---------------------------------------------------------------- 文件名识别
CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
             "七": 7, "八": 8, "九": 9, "十": 10}

ORDER_RE = re.compile(r"第\s*(\d{1,2}|[一二三四五六七八九十]{1,3})\s*次")


def pick_order(text: str, cfg: dict) -> str:
    """
    抓「第几次」，统一成 `第1次` 这种写法。
    `第1次` / `第01次` / `第一次` / `第x次作业` 都能认。
    """
    m = ORDER_RE.search(text)
    if m:
        raw = m.group(1)
        if raw.isdigit():
            return f"第{int(raw)}次"
        if raw in CN_DIGITS:
            return f"第{CN_DIGITS[raw]}次"
    if re.search(r"第\s*[xX]\s*次", text):
        return "第?次"
    return ""


def pick_name(text: str, cfg: dict) -> str:
    """从一段文字里挑出可能的姓名（先剃掉「第N次」「作业」这类噪声）。"""
    s = text
    s = ORDER_RE.sub(" ", s)
    for w in cfg["可忽略的中文词"]:
        if w:
            s = s.replace(w, " ")
    # 如果整段就是纯数字/字母，不可能是姓名
    m = re.search(cfg["姓名提取"], s)
    if not m:
        return ""
    name = m.group(0)
    if name in cfg["可忽略的中文词"]:
        return ""
    return name


def stem_clean(stem: str, cfg: dict) -> str:
    """把文件名主体里的「作业」「第1次」「副本」「(1)」等噪声去掉。"""
    s = ORDER_RE.sub(" ", stem)          # 先干掉「第N次」
    for w in cfg["可忽略的中文词"]:
        if w:
            s = s.replace(w, " ")
    # 去掉括号内容：(1)（1）【1】[1]
    s = re.sub(r"[（(\[【][^）)\]】]{0,20}[）)\]】]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip(" -_—–·、,，.。+~")


def parse_by_separator(stem: str, sep: str, cfg: dict) -> dict:
    """
    按分隔符「定位解析」——这是最准的方式。

    你本班的格式是： 班级+课程+学号+姓名+第N次作业.pdf
    于是只要按 `+` 切开，找到「那一节是学号」，它后面一节就是姓名。
    不去猜、不做正则混战，命中率接近 100%。
    """
    out = {"学号": "", "姓名": "", "次序": "", "课程": "", "班级": ""}
    sid_re = re.compile(cfg["学号提取"])

    parts = [p.strip() for p in stem.split(sep)]
    parts = [p for p in parts if p]

    # 1) 找学号所在的那一节（优先「整节就是纯数字」的）
    sid_idx = -1
    for i, p in enumerate(parts):
        if sid_re.fullmatch(p):
            sid_idx, out["学号"] = i, p
            break
    if sid_idx < 0:
        for i, p in enumerate(parts):
            m = sid_re.search(p)
            if m:
                sid_idx, out["学号"] = i, m.group(0)
                out["次序"] = out["次序"] or pick_order(p, cfg)
                break

    if sid_idx < 0:
        return out  # 这文件名压根没有学号

    used = {sid_idx}

    # 2) 姓名 = 学号后面第一节能当名字的；没有就往前找
    candidates = list(range(sid_idx + 1, len(parts))) + \
        list(range(sid_idx - 1, -1, -1))
    for i in candidates:
        nm = pick_name(parts[i], cfg)
        if nm:
            out["姓名"] = nm
            used.add(i)
            break

    # 3) 次序 = 学号后面第一节带「第N次」的；没有就全文找
    if not out["次序"]:
        for i in range(sid_idx + 1, len(parts)):
            o = pick_order(parts[i], cfg)
            if o:
                out["次序"] = o
                used.add(i)
                break
    if not out["次序"]:
        out["次序"] = pick_order(stem, cfg)

    # 4) 剩下的节：含中文的当「课程」（学号前面的优先，避免把「修改后」这种误当课程），
    #    形如 I / II / 2301 的当「班级」
    before, after = "", ""
    for i, p in enumerate(parts):
        if i in used:
            continue
        if re.fullmatch(r"[IVXivx]{1,4}", p) or re.fullmatch(r"[A-Za-z]?\d{2,4}", p):
            out["班级"] = out["班级"] or p
        elif (2 <= len(p) <= 12
              and re.search(r"[\u4e00-\u9fa5]", p)
              and not pick_order(p, cfg)
              and p not in cfg["可忽略的中文词"]):
            # 课程名常带字母后缀（高等数学I、线性代数A），所以只要求「含中文」而不是「纯中文」
            if i < sid_idx:
                if len(p) > len(before):
                    before = p
            elif len(p) > len(after):
                after = p
    out["课程"] = before or after
    return out


def parse_by_heuristic(stem: str, cfg: dict) -> dict:
    """没有分隔符时的兜底：洗噪声 -> 抓学号 -> 抓姓名（优先学号紧后面那个中文词）。"""
    out = {"学号": "", "姓名": "", "次序": "", "课程": "", "班级": ""}
    cleaned = stem_clean(stem, cfg)
    out["次序"] = pick_order(stem, cfg)

    m = re.search(cfg["学号提取"], cleaned)
    if m:
        out["学号"] = m.group(0)
        # 关键：只取学号「紧后面」那一小段，避免把前面的课程名当成姓名
        after = cleaned[m.end():m.end() + 12]
        before = cleaned[max(0, m.start() - 12):m.start()]
        out["姓名"] = pick_name(after, cfg) or pick_name(before, cfg)
        if not out["姓名"]:
            rest = cleaned[:m.start()] + " " + cleaned[m.end():]
            out["姓名"] = pick_name(rest, cfg)
    else:
        out["姓名"] = pick_name(cleaned, cfg)
    return out


def extract_fields(stem: str, cfg: dict, roster: dict) -> dict:
    """
    从文件名主体里抽出身份信息。

    两条路：
      有分隔符（如 `+`）-> 定位解析，最准
      没分隔符         -> 启发式兜底
    最后统一用学生名单校正。
    """
    seps = [s for s in (cfg.get("文件名分隔符") or []) if s]
    sep = next((s for s in seps if s in stem), "")
    fields = parse_by_separator(stem, sep, cfg) if sep else parse_by_heuristic(stem, cfg)
    if not fields["学号"] and not fields["姓名"]:
        fields = parse_by_heuristic(stem, cfg)

    sid, name = fields["学号"], fields["姓名"]

    # ---- 学生名单校正 ----
    n2s = name_to_sid(roster)
    real_sids = [s for s in roster if not s.startswith("@")]
    if sid and sid in roster and roster.get(sid):
        name = roster[sid]
    elif not sid and name and name in n2s:
        sid = n2s[name]
    elif real_sids and cfg.get("有名单时只认名单", True):
        # 配了名单就以名单为准：对不上号的宁可判为「未识别」，
        # 也不要瞎猜一个名字，避免把别人的作业算错人。
        if sid not in roster:
            sid, name = "", ""

    fields["学号"], fields["姓名"] = sid, name
    return fields


def extract_identity(stem: str, cfg: dict, roster: dict) -> tuple[str, str]:
    """从文件名主体里抽出 (学号, 姓名)。"""
    f = extract_fields(stem, cfg, roster)
    return f["学号"], f["姓名"]


def build_target_name(cfg: dict, fields: dict, ext: str, orig_stem: str = "") -> str:
    """
    按命名模板拼出归档文件名。{次序}/{课程}/{班级} 没有内容时会是空串。

    这里有一处自动纠正：如果「作业名称」里的第N次和文件里识别出的对不上，
    **以文件里识别出的为准**。否则会出现「文件名写着第2次、人却躺在
    第1次作业文件夹里」这种自相矛盾的情况 —— 补交老作业时最容易碰到。
    """
    job = str(cfg["作业名称"])
    order = str(fields.get("次序") or "")
    if order and order != "第?次":
        m = ORDER_RE.search(job)
        if m and pick_order(job, cfg) != order:
            job = job[:m.start()] + order + job[m.end():]

    text = cfg["命名模板"].format(
        作业名称=job,
        学号=fields.get("学号") or cfg["未知学号占位"],
        姓名=fields.get("姓名") or cfg["未知姓名占位"],
        次序=fields.get("次序", ""),
        课程=fields.get("课程", ""),
        班级=fields.get("班级", ""),
        原文件名=orig_stem,
        后缀=ext,
        时间=stamp(),
    )
    # 模板里缺省字段留下的空档（如 `电工__张三.docx`）顺手清理一下
    text = re.sub(r"[_\-\s]{2,}", "_", text).strip("_- ")
    return safe_filename(text)


# ---------------------------------------------------------------- 单个文件处理
def is_wanted(path: Path, cfg: dict) -> bool:
    name = path.name
    low = name.lower()
    if low.startswith("~$"):
        return False
    for bad in cfg["忽略文件名包含"]:
        if bad and bad.lower() in low:
            return False
    if path.suffix.lower() not in [e.lower() for e in cfg["文件后缀"]]:
        return False
    try:
        if path.stat().st_size < cfg["最小文件大小"]:
            return False
    except OSError:
        return False
    return True


def is_settled(path: Path, cfg: dict) -> bool:
    """文件是否已经下载/写入完成（用「修改时间够久」来判断）。"""
    try:
        st = path.stat()
    except OSError:
        return False
    age = time.time() - st.st_mtime
    if age < 0:
        # 时间戳来自未来（系统时钟偏差，或从别的机器、U 盘拷来的文件）。
        # 按原逻辑这个文件永远等不到「够久」，会被一直跳过、永远收不进来，这里直接放行。
        age = cfg["稳定等待秒"]
    return age >= cfg["稳定等待秒"]


def unique_path(p: Path) -> Path:
    """给重名文件加 (2)(3) 后缀，避免覆盖。"""
    if not p.exists():
        return p
    stem, ext, i = p.stem, p.suffix, 2
    while True:
        cand = p.with_name(f"{stem}({i}){ext}")
        if not cand.exists():
            return cand
        i += 1


def park_duplicate(dest: Path, cfg: dict) -> Path | None:
    """把一份重复文件挪进 _重复文件/ 留档。"""
    ad = archive_dir(cfg)
    target = unique_path(ad / DUP_DIR / f"{dest.stem}__{stamp()}{dest.suffix}")
    try:
        shutil.move(str(dest), str(target))
        return target
    except OSError as e:
        log(cfg, f"  ! 重复文件挪动失败：{e}")
        return None


def park_copy(src: Path, dest: Path, cfg: dict) -> Path:
    """把「重复提交」的那一份复制进 _重复文件/ 留档。"""
    ad = archive_dir(cfg)
    target = unique_path(ad / DUP_DIR / f"{dest.stem}__{stamp()}{dest.suffix}")
    try:
        if cfg["归档方式"] == "移动":
            shutil.move(str(src), str(target))
        else:
            shutil.copy2(str(src), str(target))
    except OSError as e:
        log(cfg, f"  ! 重复文件留档失败：{e}")
    return target


def process_file(path: Path, cfg: dict, state: dict, roster: dict,
                 source: str = "本地") -> dict:
    """
    处理一个文件，返回结果字典：
        {"状态": "已归档"|"重复"|"跳过"|"失败", "目标": ..., "学号":..., "姓名":...}
    """
    ad = archive_dir(cfg)
    result = {"状态": "跳过", "来源文件": str(path), "学号": "", "姓名": "",
              "目标": "", "大小": 0, "时间": now_str(), "来源": source}
    try:
        st = path.stat()
    except OSError:
        result["状态"] = "失败"
        return result
    result["大小"] = st.st_size

    fields = extract_fields(path.stem, cfg, roster)
    sid, name = fields["学号"], fields["姓名"]
    result["学号"], result["姓名"] = sid, name

    ext = path.suffix.lower()
    dest_name = build_target_name(cfg, fields, ext, path.stem)
    sub = order_folder(cfg, fields)          # 例如 `第1次作业`；关掉分文件夹时是空串
    unknown = (not sid) and (not name)
    if unknown:
        # 认不出人的文件：原样丢进 _未识别/，**保留原始文件名**。
        # 不套命名模板，是因为所有「未知」文件会共用同一个占位名，
        # 第二个起就会被误判成「重复提交」藏进 _重复文件，你在 _未识别 里会漏看。
        # 也不按次序分：认不出人先别猜是哪一次，等你核对完再手动归位。
        dest = ad / UNKNOWN_DIR / safe_filename(path.name)
    else:
        dest = (ad / sub / dest_name) if sub else (ad / dest_name)

    # ---- 去重 ----
    if dest.exists():
        policy = cfg["重复策略"]
        if policy == "全部保留":
            dest = unique_path(dest)
        else:
            old_m = dest.stat().st_mtime
            new_wins = (st.st_mtime > old_m) if policy == "最新" else (st.st_mtime < old_m)
            if new_wins:
                old = park_duplicate(dest, cfg)
                log(cfg, f"  重复：{path.name} 比已归档版本更新，替换归档"
                         f"（旧版已移到 {DUP_DIR}/{old.name if old else '?'}）")
            else:
                parked = park_copy(path, dest, cfg)
                result["状态"] = "重复"
                result["目标"] = str(parked)
                log(cfg, f"  重复提交：{path.name} 已留档为 {DUP_DIR}/{parked.name}")
                # 必须记进 state，否则下次扫描会把这个文件再留档一遍，
                # 后台每 5 秒一轮，_重复文件/ 会瞬间堆成几千份。
                state[str(path.resolve())] = {
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                    "目标": str(parked),
                    "时间": now_str(),
                }
                return result

    # ---- 搬运 ----
    dest = unique_path(dest)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)   # 次序文件夹可能还没建过
        if cfg["归档方式"] == "移动":
            shutil.move(str(path), str(dest))
        else:
            shutil.copy2(str(path), str(dest))
    except OSError as e:
        result["状态"] = "失败"
        log(cfg, f"  ! 写入失败 {path.name}：{e}")
        return result

    result["状态"] = "已归档"
    result["目标"] = str(dest)
    try:
        shown = dest.relative_to(ad)     # 日志里显示「第1次作业/xxx.pdf」，一看就懂
    except ValueError:
        shown = Path(dest.name)
    if unknown:
        log(cfg, f"  ? 【未识别】{path.name}  ->  已放进 {UNKNOWN_DIR}/，"
                 f"等你手动核对  ({human_size(st.st_size)})")
    else:
        log(cfg, f"  ✓ {path.name}  ->  {shown}  ({human_size(st.st_size)})")

    state[str(path.resolve())] = {
        "size": st.st_size,
        "mtime": st.st_mtime,
        "目标": str(dest),
        "时间": now_str(),
    }
    return result


# ---------------------------------------------------------------- 扫描 / 监控
def watch_targets(cfg: dict) -> list[tuple[Path, bool]]:
    """
    得出「该盯着哪些目录」，以及每个目录该用松还是严的规则。

      · 宽松（False）：专门的收作业目录 —— QQ 的接收目录、你明确指定的文件夹。
        这里的文件基本就是作业，按后缀收即可，认不出人的丢进 _未识别/ 让你核对。

      · 严格（True）：下载 / 桌面 / 文档 / 微信 这类「什么文件都有」的目录。
        只挑「文件名里带本班学号」的，其他一律当空气 ——
        免得把你的课表、竞赛资料、安装包一起卷进作业归档。

    返回 [(目录, 是否严格), ...]
    """
    targets: list[tuple[Path, bool]] = []
    for d in resolve_watch_dirs(cfg):
        targets.append((d, is_mixed_dir(d)))
    for p in search_roots(cfg):
        if not any(p == d for d, _ in targets):
            targets.append((p, True))
    return targets


def scan_dirs(dirs: list[Path], cfg: dict, state: dict, roster: dict,
              source: str = "本地", quiet: bool = False,
              strict: bool = False, depth: int | None = None) -> list[dict]:
    """
    把若干目录扫一遍，处理所有「还没处理过」的新文件。

    strict=True 时只收「文件名能认出本班学号」的文件（给下载/桌面这类混合目录用）。
    """
    results: list[dict] = []
    # 顺手把归档根目录里散落的旧作业归进对应的次序文件夹（幂等，归好后就没事干了）。
    # 放这里是因为下面这些入口最后都会走到 scan_dirs：扫描 / 监控 / 图形界面。
    try:
        reorganize(cfg)
    except Exception:  # noqa: BLE001  整理失败不该挡住收作业
        pass
    if depth is None:
        depth = max(0, int(cfg.get("最大扫描深度", 3)))
    ex = exclude_roots(cfg)
    for d in dirs:
        d = Path(d)
        if not d.is_dir():
            continue
        for p in sorted(walk_files(d, depth, exclude=ex), key=lambda x: str(x)):
            key = str(p.resolve())
            try:
                st = p.stat()
            except OSError:
                continue

            old = state.get(key)
            if old and old.get("size") == st.st_size and abs(old.get("mtime", 0) - st.st_mtime) < 1:
                continue  # 处理过且没变
            if strict:
                if looks_like_homework(p, cfg, roster) is None:
                    continue
            elif not is_wanted(p, cfg):
                continue
            if not is_settled(p, cfg):
                if not quiet:
                    print(f"  ... {p.name} 还在传输中，稍后再来")
                continue
            results.append(process_file(p, cfg, state, roster, source))
    if results:
        save_state(cfg, state)
    return results


def watch_loop(cfg: dict, stop_flag=None) -> None:
    """后台常驻：每 N 秒扫一次监控目录。"""
    targets = watch_targets(cfg)
    loose = [d for d, strict in targets if not strict]
    mixed = [d for d, strict in targets if strict]
    if not loose and not mixed:
        log(cfg, "没有找到可监控的目录。请先运行 `python run.py probe`，"
                 "或手动把 QQ 接收目录填进 config.json 的「监控目录」。")
        return

    ad = archive_dir(cfg)
    log(cfg, "=" * 62)
    log(cfg, f"开始监控（每 {cfg['轮询间隔秒']} 秒一次），作业名：{cfg['作业名称']}")
    log(cfg, f"归档目录：{ad}")
    for d in loose:
        log(cfg, f"  监控：{d}    （收作业目录）")
    for d in mixed:
        log(cfg, f"  监控：{d}    （混合目录，只挑本班学号的作业）")
    log(cfg, "按 Ctrl+C 停止。")
    log(cfg, "=" * 62)

    state = load_state(cfg)
    roster = load_roster(cfg)

    # ---- 第一步：先把「你打开程序之前」就已经收到的作业捞一遍 ----
    # 这一遍是严格搜索，会把下载 / 桌面 / 文档 里躺着的旧作业全找出来。
    if cfg.get("启动时搜索历史作业", True):
        log(cfg, "先搜一遍历史作业（你打开程序之前收到的）……")
        try:
            rep = search_homework(cfg, roster, state, quiet=True)
            if rep["找到"]:
                log(cfg, f"历史搜索：找到 {len(rep['找到'])} 份，"
                         f"其中本次新归档 "
                         f"{sum(1 for r in rep['结果'] if r['状态'] == '已归档')} 份。")
            else:
                log(cfg, "历史搜索：没有发现还没收的旧作业。")
        except Exception as e:  # noqa: BLE001
            log(cfg, f"! 历史搜索出错（不影响正常监控）：{e}")

    # ---- 第二步：把监控目录已有的文件也扫一遍 ----
    if cfg.get("启动时扫描已有文件"):
        got = scan_dirs(loose, cfg, state, roster, source="启动扫描")
        if mixed:
            got += scan_dirs(mixed, cfg, state, roster,
                             source="启动扫描", strict=True, depth=1)
        if got:
            log(cfg, f"启动扫描完成，处理了 {len(got)} 个文件。")

    while True:
        if stop_flag is not None and stop_flag.is_set():
            break
        try:
            got = scan_dirs(loose, cfg, state, roster, quiet=True)
            if mixed:
                # 混合目录（下载/桌面）用严格规则、只扫一层，免得每轮都翻箱倒柜
                got += scan_dirs(mixed, cfg, state, roster, quiet=True,
                                 strict=True, depth=1)
            if got:
                build_report(cfg, roster, quiet=True)
        except KeyboardInterrupt:
            break
        except Exception as e:  # noqa: BLE001  常驻程序不能因为单次异常退出
            log(cfg, f"! 轮询出错（已忽略继续）：{e}")
        time.sleep(max(1, int(cfg["轮询间隔秒"])))


# ---------------------------------------------------------------- 统计报表
def parse_archived_name(filename: str, cfg: dict) -> tuple[str, str]:
    """从归档文件名反推 (学号, 姓名)。"""
    stem = Path(filename).stem
    tokens = [t for t in re.split(r"[_\-\s]+", stem) if t]
    sid_re = re.compile(cfg["学号提取"])
    sid, name = "", ""
    for i, tok in enumerate(tokens):
        if sid_re.fullmatch(tok):
            sid = tok
            if i + 1 < len(tokens):
                name = tokens[i + 1]
            break
    if not sid:
        for tok in tokens:
            if re.fullmatch(r"[\u4e00-\u9fa5]{2,5}", tok):
                name = tok
                break
    return sid, name


def build_report(cfg: dict, roster: dict | None = None, quiet: bool = False) -> dict:
    """
    扫描归档目录，生成 _收集情况.csv 和 _未交名单.csv。

    归档目录是按次序分文件夹的（第1次作业/、第2次作业/……），
    这里**只统计当前这一次**（次序取自 config 的「作业名称」）——
    否则收了第二次之后，第一次的人会被算进「已交」，未交名单就失真了。
    归档根目录自身也扫，用来兼容老版本平铺的文件。
    """
    ad = archive_dir(cfg)
    if roster is None:
        roster = load_roster(cfg)

    exts = [e.lower() for e in cfg["文件后缀"]]
    cur = order_letter(cfg)          # 当前这次作业的次序，例如 `第1次`
    took: dict[str, dict] = {}
    sizes: dict[str, int] = {}

    for d in work_dirs(cfg):
        if d != ad and cur:
            d_order = pick_order(d.name, cfg)
            if d_order and d_order != cur:
                continue             # 别的次数的作业文件夹，不参与本次统计
        sub = "" if d == ad else d.name
        try:
            entries = sorted(d.iterdir())
        except OSError:
            continue
        for p in entries:
            if not p.is_file() or p.suffix.lower() not in exts or p.name.startswith("_"):
                continue
            sid, name = parse_archived_name(p.name, cfg)
            if not sid:
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            # 同一个学号有多份时，保留文件更大的那份作为代表
            if sid in took and st.st_size <= sizes[sid]:
                continue
            sizes[sid] = st.st_size
            took[sid] = {
                "学号": sid, "姓名": name or roster.get(sid, ""),
                "次序": sub or pick_order(p.stem, cfg),
                "归档文件名": p.name, "大小": human_size(st.st_size),
                "归档时间": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
            }

    with open(ad / REPORT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, extrasaction="ignore",
                           fieldnames=["学号", "姓名", "次序", "归档文件名", "大小", "归档时间"])
        w.writeheader()
        for sid in sorted(took):
            w.writerow(took[sid])

    missing = [(sid, nm) for sid, nm in sorted(roster.items())
               if not sid.startswith("@") and sid not in took]
    with open(ad / MISSING_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["学号", "姓名"])
        w.writerows(missing)

    summary = {
        "应有": len([s for s in roster if not s.startswith("@")]),
        "已收": len([s for s in took if s in roster]),
        "额外": sorted(s for s in took if roster and s not in roster),
        "未识别学号": sorted(s for s in took if not roster),
        "未交": missing,
    }

    if not quiet:
        print()
        print("=" * 62)
        print(f"作业名称：{cfg['作业名称']}")
        if summary["应有"]:
            print(f"应收 {summary['应有']} 人，已收 {summary['已收']} 人，"
                  f"未交 {len(missing)} 人")
        else:
            print(f"已归档 {len(took)} 份（没有配置学生名单，无法统计未交）")
        if missing:
            print("-" * 62)
            print("未交名单：")
            line = "  "
            for sid, nm in missing:
                item = f"{nm}({sid})" if nm else sid
                if len(line) + len(item) > 58:
                    print(line)
                    line = "  "
                line += item + "  "
            if line.strip():
                print(line)
        if summary["额外"]:
            print("-" * 62)
            print("名单外的学号（可能是新同学或录错了）：" + "、".join(summary["额外"]))
        unk_dir = ad / UNKNOWN_DIR
        unk = sorted(p for p in unk_dir.iterdir()
                     if p.is_file()) if unk_dir.is_dir() else []
        if unk:
            print("-" * 62)
            print(f"⚠ {len(unk)} 个文件没认出是谁，躺在 {UNKNOWN_DIR}/ 里等你手动核对：")
            for p in unk[:5]:
                print(f"    {p.name}")
            if len(unk) > 5:
                print(f"    …… 还有 {len(unk) - 5} 个")
        print("-" * 62)
        print(f"明细表：{ad / REPORT_CSV}")
        print(f"未交表：{ad / MISSING_CSV}")
        print(f"归档文件夹：{ad}")
        print("=" * 62)
    return summary


# ---------------------------------------------------------------- 一键打包
def pack_archive(cfg: dict, roster: dict | None = None, as_zip: bool = False,
                 out_dir=None, quiet: bool = False) -> Path:
    """
    把归档结果整理成一个「可以直接交给老师」的干净文件夹。

    - 只拿归档目录**根下**的作业文件；`_未识别/`、`_重复文件/`、`_处理记录.json`、
      `_日志.log` 这些内部东西一律不带（老师不需要看，也免得解释）
    - 附上两张表，重命名成一眼能看懂的中文名
    - 默认输出到程序旁边，文件夹名 = `打包_作业名称_日期`。
      **同一天重复打包会复用同一个文件夹**（先清空再重建），不会出现
      `xxx(2)`、`xxx(3)` 越堆越多；前缀也让清理功能认得出来。

    返回生成的文件夹（或 zip）路径。
    """
    ad = archive_dir(cfg)
    if roster is None:
        roster = load_roster(cfg)
    build_report(cfg, roster, quiet=True)   # 打包前先刷新统计，保证表是最新的

    exts = [e.lower() for e in cfg["文件后缀"]]
    cur = order_letter(cfg)
    works: list[Path] = []
    for d in work_dirs(cfg):
        if d != ad and cur:
            d_order = pick_order(d.name, cfg)
            if d_order and d_order != cur:
                continue             # 只打包这一次的，别的次数不掺进来
        try:
            entries = sorted(d.iterdir())
        except OSError:
            continue
        works += [p for p in entries
                  if p.is_file() and not p.name.startswith("_")
                  and p.suffix.lower() in exts]
    unk_dir = ad / UNKNOWN_DIR
    unknowns = [p for p in unk_dir.iterdir() if p.is_file()] if unk_dir.is_dir() else []

    base = Path(out_dir) if out_dir else base_dir()
    folder = safe_filename(f"{PACK_PREFIX}{cfg['作业名称']}_{datetime.now():%Y%m%d}")
    target = base / folder
    # 同一天重复打包 -> 复用同一个文件夹（清空重建），不生成 xxx(2)、xxx(3)。
    # 只删自己命名的目录，别的一概不碰。
    if target.is_dir() and target.name.startswith(PACK_PREFIX):
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)

    for p in works:
        shutil.copy2(str(p), str(target / p.name))

    for src, dst_name in ((ad / REPORT_CSV, "已交名单.csv"),
                          (ad / MISSING_CSV, "未交名单.csv")):
        if src.exists():
            shutil.copy2(str(src), str(target / dst_name))

    todo = [s for s in roster if not s.startswith("@")] if roster else []
    should = len(todo) if todo else len(works)
    lines = [
        f"{cfg['作业名称']} —— 作业收集结果",
        f"打包时间：{now_str()}",
        "",
        f"应收 {should} 人，已收 {len(works)} 人，未交 {max(0, should - len(works))} 人",
        "",
        "已交名单.csv —— 谁交了、交的是哪份文件、多大、什么时候",
        "未交名单.csv —— 还差谁没交",
    ]
    if unknowns:
        lines += ["",
                  f"注意：另有 {len(unknowns)} 个文件没认出是谁，没放进这个文件夹，"
                  f"需要你自己核对（在归档目录的 _未识别 里）。"]
    (target / "说明.txt").write_text("\n".join(lines) + "\n", encoding="utf-8-sig")

    if as_zip:
        zpath = shutil.make_archive(str(target), "zip", root_dir=str(target))
        shutil.rmtree(target, ignore_errors=True)
        result = Path(zpath)
    else:
        result = target

    if not quiet:
        print(f"打包完成：{len(works)} 份作业 -> {result}")
        if unknowns:
            print(f"（另有 {len(unknowns)} 个未识别文件没放进来，等你手动核对）")
    return result


# ---------------------------------------------------------------- 冗余文件清理
# 只认这三类「程序自己产生的、删掉不会丢任何作业」的东西。
# 你的作业文件、学生名单、统计表一律不在清理范围内。
JUNK_CACHE = "缓存垃圾"       # __pycache__ / 启动器缓存 / 旧日志，删了会自动重建
JUNK_DEMO = "演练残留"        # run.py demo 造出来的假作业
JUNK_DUP = "重复副本"         # 同一个学号在归档里有多份（反复提交留下的）
JUNK_PACK = "打包产物"        # 旧的「打包_xxx」文件夹/压缩包，只留最新一份
JUNK_OLD_DUP = "重复留档"     # _重复文件/ 里积压的历史版本，每学号留最新一份


def _path_size(p: Path) -> int:
    """文件或整个目录占多少字节。"""
    try:
        if p.is_file():
            return p.stat().st_size
        total = 0
        for f in p.rglob("*"):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
        return total
    except OSError:
        return 0


def _paths_size(items: list[Path]) -> int:
    return sum(_path_size(p) for p in items)


def scan_junk(cfg: dict, include_demo: bool = True) -> dict:
    """
    找出「冗余文件」——程序自己产生的、删掉不会丢任何作业的东西。

    **只看不动**，清理交给 purge_junk()。返回五类：

      · 缓存垃圾   —— `__pycache__`、`_python_path.txt`、`_启动失败.log`、旧日志
                     删了下次运行会自动重建，不影响任何数据
      · 演练残留   —— `demo-raw/`、`demo-归档/`，`run.py demo` 造出来的假作业
      · 重复副本   —— 同一个学号在归档里有多份文件（可能是反复提交留下的），
                     每组保留最大的那份，其余列为候选
      · 打包产物   —— 旧的 `打包_xxx/` 文件夹或 zip，**保留最新一份**，
                     其余列为候选（打包是复制，旧的就是纯占地方）
      · 重复留档   —— `_重复文件/` 里同一个人积压的历史版本，每学号留最新一份

    你的作业文件、学生名单、`_收集情况.csv` 都不会出现在这里。
    """
    bd = base_dir()
    ad = archive_dir(cfg)

    cache: list[Path] = []
    for name in ("__pycache__",):
        p = bd / name
        if p.is_dir():
            cache.append(p)
    for name in ("_python_path.txt", "_启动失败.log"):
        p = bd / name
        if p.is_file():
            cache.append(p)
    # 日志换代时留下的上一代（收一学期也才几百 KB，纯粹是省地方）
    p = ad / LOG_OLD_NAME
    if p.is_file():
        cache.append(p)
    # 回收站不可用时的临时备份（正常情况下不会出现，出现了就是上次没删干净）
    for p in sorted(bd.glob("_待清理备份_*")):
        if p.is_dir():
            cache.append(p)

    demo: list[Path] = []
    if include_demo:
        for name in ("demo-raw", "demo-归档"):
            p = bd / name
            if p.is_dir():
                demo.append(p)

    # ---- 打包产物：只留最新的一份，其余列候选 ----
    # 打包是「复制」出去的，同一份作业会有多份副本，而正常打包只会留一份。
    # 每收一次作业打一次包、日积月累就是这个目录最大的增长源（实测一份作业
    # 40 MB，打包 3 次就是 120 MB），所以这里兜底清掉旧的。
    packs: list[Path] = []
    for p in bd.glob(PACK_PREFIX + "*"):
        try:
            if p.is_dir() or p.suffix.lower() == ".zip":
                packs.append(p)
        except OSError:
            continue
    packs.sort(key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True)
    pack_old = packs[1:]          # 最新的那份留着 —— 你可能刚打包完正要发给老师

    # ---- _重复文件/ 里的积压：每个学号留最新 1 份 ----
    old_dup: list[Path] = []
    dd = ad / DUP_DIR
    if dd.is_dir():
        g2: dict[str, list[Path]] = {}
        try:
            for p in dd.iterdir():
                if not p.is_file():
                    continue
                sid, _n = parse_archived_name(p.name, cfg)
                g2.setdefault(sid or p.stem, []).append(p)
        except OSError:
            pass
        for _key, ps in g2.items():
            if len(ps) <= 1:
                continue
            ps.sort(key=lambda x: (x.stat().st_mtime if x.exists() else 0, x.name),
                    reverse=True)
            old_dup += ps[1:]

    # ---- 归档里的重复副本 ----
    exts = [e.lower() for e in cfg["文件后缀"]]
    groups: dict[str, list[Path]] = {}
    for d in work_dirs(cfg):
        try:
            entries = sorted(d.iterdir())
        except OSError:
            continue
        for p in entries:
            if not p.is_file() or p.suffix.lower() not in exts or p.name.startswith("_"):
                continue
            sid, _name = parse_archived_name(p.name, cfg)
            if sid:
                groups.setdefault(sid, []).append(p)

    dup_groups: dict[str, list[Path]] = {}
    dupes: list[Path] = []
    for sid, ps in groups.items():
        if len(ps) <= 1:
            continue
        ps = sorted(ps, key=lambda x: (-_path_size(x), x.name))
        dup_groups[sid] = ps
        dupes += ps[1:]              # 每组留最大的那份当代表

    return {
        JUNK_CACHE: cache,
        JUNK_DEMO: demo,
        JUNK_DUP: dupes,
        JUNK_PACK: pack_old,
        JUNK_OLD_DUP: old_dup,
        "重复分组": dup_groups,
        "全部打包产物": packs,
        "可清理体积": (_paths_size(cache) + _paths_size(demo)
                       + _paths_size(dupes) + _paths_size(pack_old)
                       + _paths_size(old_dup)),
        "归档目录": ad,
    }


def send_to_trash(paths: list[Path]) -> tuple[bool, str]:
    """
    把文件/目录丢进 **Windows 回收站**（不是永久删除）。

    用系统自带的 SHFileOperation，带 FOF_ALLOWUNDO 标志 ——
    万一删错了，去回收站右键「还原」就能拿回来。

    非 Windows 或调用失败时返回 (False, 原因)，由调用方决定要不要退化成
    「移到备份文件夹」，总之**绝不静默永久删除**。
    """
    if os.name != "nt":
        return False, "当前系统不是 Windows，不支持回收站"

    items = [str(Path(p)) for p in paths if Path(p).exists()]
    if not items:
        return True, "没有需要清理的东西"
    if len(items) > 200:
        return False, f"一次要删 {len(items)} 项，太多了，为安全起见请分批"

    try:
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("wFunc", wintypes.UINT),
                ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR),
                ("fFlags", ctypes.c_ushort),
                ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", wintypes.LPCWSTR),
            ]

        FO_DELETE = 3
        FOF_SILENT = 0x0004
        FOF_NOCONFIRMATION = 0x0010
        FOF_ALLOWUNDO = 0x0040          # 关键：允许撤销 -> 进回收站而不是抹掉
        FOF_NOERRORUI = 0x0400

        # pFrom 要求「每一项用 \0 分隔、最后一个后面再补一个 \0」。
        # 必须用 create_unicode_buffer 并把缓冲的引用一直留着：
        # 直接给 c_wchar_p 字段赋 Python 字符串的话，ctypes 临时建的缓冲
        # 可能在真正调用前就被回收，SHFileOperation 会悄悄失败（实测踩过，rc≠0）。
        buf = ctypes.create_unicode_buffer("\0".join(items) + "\0\0")
        op = SHFILEOPSTRUCTW()
        op.hwnd = None
        op.wFunc = FO_DELETE
        op.pFrom = ctypes.cast(buf, ctypes.c_wchar_p)
        op.pTo = None
        op.fFlags = (FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI)

        ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        if op.fAnyOperationsAborted:
            return False, "操作被中断"
    except Exception as e:  # noqa: BLE001
        return False, f"调用回收站失败：{e}"

    # 成功与否看「东西还在不在」，**不看返回值** ——
    # 实测 SHFileOperationW 明明把文件送进了回收站，rc 也可能是 2，
    # 拿 rc 当判据会把成功的操作误判成失败，白留一个备份文件夹。
    left = [Path(p) for p in paths if Path(p).exists()]
    if left:
        names = "、".join(p.name for p in left[:5])
        return False, (f"有 {len(left)} 项没能移入回收站（{names}）——"
                       f"可能正被别的程序占用")
    return True, f"已把 {len(items)} 项移入回收站（可随时还原）"


def _move_to_backup(paths: list[Path], cfg: dict) -> tuple[bool, str]:
    """回收站不可用时的退路：移到程序旁边的 `_待清理备份_日期/`，绝不真删。"""
    rest = [Path(p) for p in paths if Path(p).exists()]
    if not rest:
        return True, "目标已经不在了，无需备份"
    bak = base_dir() / f"_待清理备份_{datetime.now():%Y%m%d_%H%M%S}"
    try:
        bak.mkdir(parents=True, exist_ok=True)
        for p in rest:
            shutil.move(str(p), str(bak / p.name))
    except OSError as e:
        return False, f"备份移动失败：{e}"
    return True, f"回收站不可用，已改放到 {bak}（你自己确认后再删）"


def purge_junk(cfg: dict, kinds=None, use_trash: bool = True,
               quiet: bool = False) -> dict:
    """
    执行清理。默认清三类 —— 缓存垃圾、演练残留、旧的打包产物。
    这些 100% 是程序自己产生的副本，删掉不会丢任何作业
    （打包产物永远保留最新的一份）。

    「重复副本」「重复留档」需要你明确传进来才会清
    （它们可能是你有意保留的旧版本）。

    返回 {"清理": [...], "成功": bool, "说明": str, "体积": N}
    """
    if kinds is None:
        kinds = [JUNK_CACHE, JUNK_DEMO, JUNK_PACK]
    rep = scan_junk(cfg)
    targets: list[Path] = []
    for k in kinds:
        for p in rep.get(k, []):
            if p not in targets:
                targets.append(p)

    result = {"清理": [], "成功": True, "说明": "", "体积": 0,
              "分类": {k: [str(x) for x in rep.get(k, [])] for k in kinds}}
    if not targets:
        result["说明"] = "没有需要清理的东西"
        return result

    result["体积"] = _paths_size(targets)     # 先量体积，删完就量不到了

    if use_trash:
        ok, msg = send_to_trash(targets)
        if not ok:
            ok, msg = _move_to_backup(targets, cfg)
    else:
        ok, msg = _move_to_backup(targets, cfg)

    result["成功"] = ok
    result["说明"] = msg
    result["清理"] = [str(p) for p in targets]
    if not quiet:
        log(cfg, f"清理冗余：{msg}")
        for p in targets:
            log(cfg, f"  - 已清理 {p}")
    return result


# ---------------------------------------------------------------- 自检
def doctor(cfg: dict) -> None:
    """打印当前配置和探测结果，方便排查。"""
    ad = archive_dir(cfg)
    print("=" * 62)
    print("当前配置自检")
    print("=" * 62)
    print(f"作业名称        : {cfg['作业名称']}")
    print(f"归档目录        : {ad}")
    if cfg.get("按次序分文件夹", True):
        cur = order_letter(cfg)
        subs = [d.name for d in work_dirs(cfg) if d != ad]
        print("归档结构        : 按次序分子文件夹（第1次作业 / 第2次作业 / …）")
        if cur:
            print(f"                  本次是 {cur}  ->  {ad / (cur + '作业')}")
        else:
            print("                  提示：作业名称里没写「第几次」，"
                  "建议写成「高等数学I第2次作业」，程序才认得出该归到哪次")
        print(f"                  已有文件夹：{'、'.join(subs) if subs else '（还没有）'}")
    else:
        print("归档结构        : 全部平铺在归档目录里（没开分文件夹）")
    print(f"归档方式        : {cfg['归档方式']}（复制=原文件保留，移动=原文件消失）")
    print(f"重复策略        : {cfg['重复策略']}")
    print(f"命名模板        : {cfg['命名模板']}")
    seps = cfg.get("文件名分隔符") or []
    print(f"文件名分隔符    : {' '.join(seps) if seps else '（无，走智能识别）'}")
    print(f"支持的文件格式  : {len(cfg['文件后缀'])} 种")

    rp = roster_path(cfg)
    roster = load_roster(cfg)
    if roster:
        print(f"学生名单        : {rp}（{len(roster)} 人）")
    elif rp.exists():
        print(f"学生名单        : 文件在（{rp}）但里面还没有名单 ——")
        print("                  请把班级花名册粘贴进去（一行一个：学号 姓名），")
        print("                  否则认不出人、也统计不了未交名单。")
    else:
        print(f"学生名单        : 找不到文件（{rp}）")

    targets = watch_targets(cfg)
    if targets:
        print(f"监控目录        : 共 {len(targets)} 个")
        for d, strict in targets:
            n = len(list(_iter_files(d)))
            tag = "混合目录·只挑本班学号" if strict else "收作业目录"
            print(f"                  - {d}  （当前 {n} 个文件）  [{tag}]")
    else:
        print("监控目录        : 没找到！请运行 `python run.py probe` 看看 QQ 目录在哪")
    print(f"历史搜索        : {'开' if cfg.get('启动时搜索历史作业', True) else '关'}"
          f"（启动时自动翻一遍 下载/桌面/文档 里没收的旧作业）")
    print("=" * 62)


def probe(cfg: dict) -> None:
    """探测 QQ 的文件接收目录，并告诉你该监控哪个。"""
    print("=" * 62)
    print("开始探测 QQ 接收目录……")
    print("=" * 62)
    found = candidate_qq_dirs()
    picked = {p for p in pick_watch_candidates(found)}

    if not found:
        print("没有自动找到任何 QQ 目录。手动找一下（30 秒）：")
        print()
        print("  1) 打开 QQ -> 左下角「三」 -> 设置 -> 文件管理")
        print("  2) 页面上写着「文件接收目录」，点右边的「打开文件夹」")
        print("  3) 把地址栏里的路径复制下来，填进 config.json：")
        print(r'     "监控目录": ["C:\Users\你的用户名\Documents\Tencent Files\123456789\nt_qq\nt_data\File"]')
        print()
        print("或者用图形界面：双击 start_gui.bat -> 「手动选择文件夹」。")
        print("=" * 62)
        return

    for i, d in enumerate(found, 1):
        docs, total, newest = dir_activity(d)
        newest_s = (datetime.fromtimestamp(newest).strftime("%Y-%m-%d %H:%M")
                    if newest else "无")
        print(f"[{i}] {d}{'   ★ 程序会监控这里' if d in picked else ''}")
        print(f"    文档 {docs} 个 / 文件共 {total} 个     最近一个文件：{newest_s}")
        if total and not docs:
            print("    （全是图片/缓存，不像收作业的地方）")
        for f in list(_iter_files(d, limit=5)):
            print(f"      - {f.name}")

    print("-" * 62)
    if any(dir_activity(d)[0] > 0 for d in found):
        print("★ 就是程序自动选中的监控目录，通常不用手动改。")
    else:
        print("以上目录里目前都没有文档 —— 这很正常：")
        print("QQ 需要**你点过「接收 / 下载」之后**，才会把文件真正存到磁盘上；")
        print("没人给你发过作业时，这个目录本来就是空的。")
        print("★ 是程序先挂上的位置，以后收到作业会自动归档。")
    print()
    print("想知道你自己 QQ 到底是哪个路径：QQ -> 设置 -> 文件管理 -> 打开文件夹，")
    print("对照上面列表找同名的那个即可。填进 config.json 的「监控目录」，例如：")
    print(r'  "监控目录": ["C:\Users\你的用户名\Documents\Tencent Files\123456789\nt_qq\nt_data\File"]')
    print("=" * 62)


if __name__ == "__main__":
    print(__doc__)
    print("请用 run.py 启动：  python run.py")
