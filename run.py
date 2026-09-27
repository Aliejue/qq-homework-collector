# -*- coding: utf-8 -*-
"""
作业收集器 —— 手动 / 监控模式入口
================================================================
用法：

    python run.py            交互式菜单（推荐，双击 start_watcher.bat 也行）
    python run.py scan       立即扫描一次，把已有的作业整理归档
    python run.py find       找回之前的作业：翻一遍下载/桌面/文档里还没收的旧作业
    python run.py watch      后台常驻监控，来一个新文件就自动归档一个
    python run.py report     只出统计：收集明细 + 未交名单
    python run.py pack       一键打包：整理成可以直接交给老师的干净文件夹
    python run.py probe      探测 QQ 的「接收文件夹」到底在哪
    python run.py doctor     自检：打印当前配置和目录情况
    python run.py demo       生成一批假作业文件并跑一遍，用来试功能
                             （会先清空 demo-raw / demo-归档，可反复跑）
================================================================
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import collector as C  # noqa: E402


BANNER = r"""
  ╭──────────────────────────────────────────────────────────╮
  │              作  业  收  集  器   v1.0                   │
  │      QQ 收到的作业 -> 自动认人 -> 统一改名 -> 归档统计    │
  ╰──────────────────────────────────────────────────────────╯
"""


def cmd_scan(cfg) -> None:
    """
    立即扫描一次：先扫收作业目录，再满世界找一遍历史作业。

    合起来的效果就是「现在立刻把所有该收的都收掉」——
    包括你打开程序之前同学就已经发过来的那些。
    """
    targets = C.watch_targets(cfg)
    if not targets:
        print("没找到可扫描的目录。请先运行：python run.py probe")
        return
    roster = C.load_roster(cfg)
    state = C.load_state(cfg)
    loose = [d for d, strict in targets if not strict]

    got: list[dict] = []
    if loose:
        print(f"开始扫描 {len(loose)} 个收作业目录……")
        got += C.scan_dirs(loose, cfg, state, roster, source="立即扫描")

    rep = C.search_homework(cfg, roster, state, quiet=True)
    got += rep["结果"]

    if not got:
        print("没有发现新文件（已经收过的不会重复处理）。")
    else:
        ok = sum(1 for r in got if r["状态"] == "已归档")
        dup = sum(1 for r in got if r["状态"] == "重复")
        print(f"\n本次处理 {len(got)} 个文件：归档 {ok} 个，重复留档 {dup} 个。")
    C.build_report(cfg, roster)


def cmd_find(cfg) -> None:
    """只做历史搜索：把打开程序之前收到的作业全捞出来。"""
    roster = C.load_roster(cfg)
    if not roster:
        print("提示：还没填「学生名单.txt」，没法判断一个文件是不是本班的作业。")
        print("      建议先填名单，再运行本命令。\n")
    rep = C.search_homework(cfg, roster, collect=True)
    C.build_report(cfg, roster)


def cmd_watch(cfg) -> None:
    try:
        C.watch_loop(cfg)
    except KeyboardInterrupt:
        print("\n已停止监控。")


def cmd_report(cfg) -> None:
    C.build_report(cfg, C.load_roster(cfg))


def cmd_probe(cfg) -> None:
    C.probe(cfg)


def cmd_doctor(cfg) -> None:
    C.doctor(cfg)


def cmd_demo(cfg) -> None:
    """
    生成一批仿真作业文件，验证识别与归档效果。

    全部按本班的固定格式： 课程+专业+学号+姓名+第N次作业.pdf
    （故意混入几种「同学不老实」的写法，看看程序扛不扛得住）
    """
    import os
    import shutil
    import time as _time

    # 演练目录是可丢弃的：每次跑之前先清空，避免上一轮的残留干扰结果。
    # 只删名字以 "demo-" 开头的目录，不会碰用户的真实归档。
    for name in ("demo-raw", "demo-归档"):
        d = C.base_dir() / name
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)

    raw = C.base_dir() / "demo-raw"
    raw.mkdir(exist_ok=True)
    now = _time.time()

    # (文件名, 字节数, 修改时间偏移秒)  负数 = 更早
    samples = [
        ("高等数学I+EEE+2025000000001+张三+第1次作业.pdf", 4200, -7200),  # 2 小时前的老版本
        ("高等数学I+EEE+2025000000002+李四+第1次作业.pdf", 4000, -60),
        ("高等数学I+EEE+2025000000003+王五+第1次作业.pdf", 3800, -50),
        ("高等数学I+EEE+2025000000004+赵六+第1次作业.pdf", 4500, -40),
        ("EEE+2025000000005+钱七+第1次作业.pdf", 4100, -30),            # 课程名忘了写
        ("高等数学I+EEE+2025000000006+孙八+第一次作业.pdf", 3900, -20),    # 中文数字
        ("高等数学I+EEE+2025000000007+周九+第1次作业(1).pdf", 4300, -10),  # 带 (1)
        ("高等数学I+EEE+2025000000008+第1次作业.pdf", 3700, -15),          # 漏写姓名，靠名单补
        ("高等数学I+EEE+2025000000001+张三+第1次作业 修改版.pdf", 4600, 0),   # 张三重新提交
        ("高等数学I+EEE+20250000000x+未知+第1次作业.pdf", 3600, -5),        # 学号打错了
        ("新建 文本文档.txt", 1500, -5),                                 # 认不出是谁
    ]
    for name, size, offset in samples:
        p = raw / name
        p.write_bytes(b"HOMEWORK-DEMO" + bytes(size))
        ts = now + offset
        os.utime(p, (ts, ts))
    print(f"已在 {raw} 生成 {len(samples)} 个示例文件。\n")

    cfg = dict(cfg)
    cfg["作业名称"] = "示例第1次作业"   # 免得演练出来的文件跟你真实作业混在一起
    cfg["监控目录"] = [raw]
    cfg["自动探测QQ接收目录"] = False
    cfg["归档目录"] = "demo-归档"
    cfg["名单文件"] = "学生名单-示例.txt"
    cfg["稳定等待秒"] = 0   # 示例文件是刚生成的，跳过「等文件写完」这一步
    state = C.load_state(cfg)
    roster = C.load_roster(cfg)
    C.scan_dirs(cfg["监控目录"], cfg, state, roster)
    C.build_report(cfg, roster)


def cmd_pack(cfg) -> None:
    """把作业和统计表整理成一个可以直接交给老师的干净文件夹。"""
    roster = C.load_roster(cfg)
    if not roster:
        print("提示：还没填「学生名单.txt」，打包出来的表里不会有人数统计。")
    p = C.pack_archive(cfg, roster)
    print()
    print(f"这个文件夹可以直接发给老师：\n  {p}")
    print("（内部的 _处理记录.json、_日志.log 都没放进去）")


def _ask(prompt: str) -> str:
    """
    读一行输入。

    没有交互环境时（管道、被别的程序调用、输入流被关掉）input() 会抛
    EOFError 把命令直接崩掉。统一在这里兜住，一律当成「否」—— 安全的默认值。
    """
    try:
        return input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def cmd_clean(cfg) -> None:
    """
    清理冗余文件。

    只清「程序自己产生的」东西：
      · 缓存垃圾 —— __pycache__、启动器缓存、旧日志（删了会自动重建）
      · 演练残留 —— demo-raw / demo-归档（run.py demo 造的假作业）
      · 旧的打包产物 —— 打包_xxx 文件夹 / zip，永远保留最新那一份
      · 归档里的重复副本 —— 同一学号有多份，每组留最大的那份
      · 重复留档 —— _重复文件/ 里积压的旧版本，每学号留最新一份

    一律送进回收站，不是永久删除，随时能还原。
    你的作业、学生名单、统计表都不在清理范围内。
    """
    rep = C.scan_junk(cfg)
    print("=" * 62)
    print("冗余文件扫描")
    print("=" * 62)

    total = 0
    for key, note in ((C.JUNK_CACHE, "默认会清"),
                      (C.JUNK_DEMO, "默认会清"),
                      (C.JUNK_PACK, "默认会清，保留最新一份"),
                      (C.JUNK_DUP, "要你确认"),
                      (C.JUNK_OLD_DUP, "要你确认")):
        items = rep.get(key, [])
        size = sum(C._path_size(p) for p in items)
        total += size
        print(f"\n{key}　{len(items)} 项　{C.human_size(size)}　（{note}）")
        if not items:
            print("    （无）")
        for p in items[:12]:
            print(f"    {p}")
        if len(items) > 12:
            print(f"    …… 还有 {len(items) - 12} 项")

    keep_pack = rep.get("全部打包产物", [])[:1]
    if rep.get(C.JUNK_PACK) and keep_pack:
        print(f"\n（打包产物只清旧的，最新这份给你留着：{keep_pack[0].name}）")

    if total == 0:
        print("\n没有发现冗余文件，程序目录很干净。")
        print("（顺便说一句：即使这里显示「无」，也不代表作业丢了 ——")
        print("  作业都在归档目录里，那部分永远不会被清理。）")
        return

    print("\n" + "-" * 62)
    print(f"共可清理 {C.human_size(total)}。")
    print("所有文件都送进回收站，删错了随时能还原（不是永久删除）。")
    ans = _ask("清理「缓存垃圾 + 演练残留 + 旧打包产物」？(y/N) ")
    if ans not in ("y", "yes", "是"):
        print("已取消，什么都没删。")
        return
    r = C.purge_junk(cfg, kinds=[C.JUNK_CACHE, C.JUNK_DEMO, C.JUNK_PACK])
    print(f"\n{r['说明']}（{C.human_size(r['体积'])}）")

    rest = list(rep.get(C.JUNK_DUP, [])) + list(rep.get(C.JUNK_OLD_DUP, []))
    if rest:
        print()
        print(f"还剩 {len(rest)} 份「重复副本 / 重复留档」"
              f"（同一个人存了好几版；统计和打包用的都是现行版本）：")
        for p in rest[:10]:
            print(f"    {p.name}")
        if len(rest) > 10:
            print(f"    …… 还有 {len(rest) - 10} 份")
        ans2 = _ask("这些也一起清掉？(y/N) ")
        if ans2 in ("y", "yes", "是"):
            r2 = C.purge_junk(cfg, kinds=[C.JUNK_DUP, C.JUNK_OLD_DUP])
            print(f"\n{r2['说明']}（{C.human_size(r2['体积'])}）")
        else:
            print("保留，不动它们。")


def menu(cfg) -> None:
    print(BANNER)
    C.doctor(cfg)
    while True:
        print()
        print("请选择要做什么：")
        print("  1) 立即扫描一次（把已经收到的作业整理归档，含旧作业）")
        print("  2) 开始后台监控（挂着不管，来一个收一个）★ 推荐")
        print("  3) 找回之前的作业（翻一遍下载/桌面里还没收的旧作业）")
        print("  4) 只看统计（收集明细 / 未交名单）")
        print("  5) 探测 QQ 接收目录在哪")
        print("  6) 修改设置（作业名称、归档目录、名单文件等）")
        print("  7) 一键打包（整理成可以直接交给老师的干净文件夹）★ 交作业用")
        print("  0) 退出")
        choice = input("输入编号后回车：").strip()

        if choice == "1":
            cmd_scan(cfg)
        elif choice == "2":
            cmd_watch(cfg)
        elif choice == "3":
            cmd_find(cfg)
        elif choice == "4":
            cmd_report(cfg)
        elif choice == "5":
            cmd_probe(cfg)
        elif choice == "6":
            cfg = edit_config(cfg)
        elif choice == "7":
            cmd_pack(cfg)
        elif choice == "8":
            cmd_clean(cfg)
        elif choice == "0":
            print("再见～")
            return
        else:
            print("没看懂，请输入 0-8 的数字。")


def edit_config(cfg):
    print()
    print("直接回车 = 保持原值")
    print(f"当前作业名称   : {cfg['作业名称']}")
    v = input("新的作业名称（例：第三章作业）：").strip()
    if v:
        cfg["作业名称"] = v

    print(f"当前归档目录   : {cfg['归档目录']}（相对路径 = 放在程序旁边）")
    v = input("新的归档目录：").strip()
    if v:
        cfg["归档目录"] = v

    print(f"当前归档方式   : {cfg['归档方式']}（复制 / 移动）")
    v = input("新的归档方式：").strip()
    if v in ("复制", "移动"):
        cfg["归档方式"] = v

    print(f"当前重复策略   : {cfg['重复策略']}（最新 / 最早 / 全部保留）")
    v = input("新的重复策略：").strip()
    if v in ("最新", "最早", "全部保留"):
        cfg["重复策略"] = v

    C.save_config(cfg)
    print("已保存到 config.json。")
    return cfg


def main() -> None:
    C.setup_console()
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    cfg = C.load_config()
    table = {
        "scan": cmd_scan, "watch": cmd_watch, "report": cmd_report,
        "probe": cmd_probe, "doctor": cmd_doctor, "demo": cmd_demo,
        "pack": cmd_pack, "find": cmd_find, "clean": cmd_clean,
    }
    if args and args[0] in table:
        table[args[0]](cfg)
    elif args:
        print(f"未知命令：{args[0]}\n可用命令：{'、'.join(table)}")
    else:
        try:
            menu(cfg)
        except (KeyboardInterrupt, EOFError):
            print("\n再见～")


if __name__ == "__main__":
    main()
