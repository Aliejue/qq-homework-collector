# -*- coding: utf-8 -*-
"""
作业收集器 —— 图形控制台（tkinter，零依赖）
================================================================
双击 `start_gui.bat` 打开，或命令行 `python gui.py`。

界面上能做的事：
    · 开始收作业 / 停止 / 立即扫描一次
    · 一键打包给老师（文件夹或 zip）
    · 打开归档文件夹 / 编辑学生名单 / 修改设置 / 探测 QQ 接收目录

所有处理都在后台线程跑，界面不会卡；日志实时滚动。
================================================================
"""

from __future__ import annotations

import os
import queue
import re
import sys
import threading
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import collector as C  # noqa: E402

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, scrolledtext, filedialog
except ImportError:
    print("这个 Python 没有带 tkinter，用不了图形界面。")
    print("请改用命令行版：双击 start.bat")
    raise SystemExit(1)


APP = "作业收集器"
FONT = "Microsoft YaHei UI"
GRAY = "#6b7280"
GREEN = "#0f7b3f"
RED = "#b42318"
AMBER = "#b54708"
INK = "#111827"


class StdoutCatcher:
    """把 print 出来的内容塞进队列，交给界面显示（不写控制台）。"""

    def __init__(self, q: queue.Queue):
        self.q = q

    def write(self, s):
        if s:
            self.q.put(s)
        return len(s)

    def flush(self):
        pass


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = C.load_config()
        self.q: queue.Queue = queue.Queue()
        self.stop_event = None
        self.worker = None
        self.running = False
        self._watch_done = False
        self._need_refresh = False
        self._pending_dirs = None
        self._pending_clean = None
        self._finding = False
        self._find_done = False

        self._build_ui()

        sys.stdout = StdoutCatcher(self.q)
        sys.stderr = sys.stdout

        self._pump()
        self.refresh()
        self._welcome()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------ 界面
    def _build_ui(self):
        self.root.title(f"{APP} · 控制台")
        # 不用「按屏幕尺寸居中」——有些机器上 winfo_screenwidth() 报的值比实际可用宽度大，
        # 居中会把窗口右半边推出屏幕。固定放左上角，保证一定能完整显示。
        w, h = 1100, 770
        self.root.geometry(f"{w}x{h}+30+24")
        self.root.minsize(960, 660)

        head = ttk.Frame(self.root, padding=(16, 14, 16, 2))
        head.pack(fill="x")
        self.lbl_job = ttk.Label(head, text="", font=(FONT, 16, "bold"), foreground=INK)
        self.lbl_job.pack(anchor="w")
        self.lbl_dir = ttk.Label(head, text="", font=(FONT, 9), foreground=GRAY)
        self.lbl_dir.pack(anchor="w", pady=(3, 0))

        cards = ttk.Frame(self.root, padding=(16, 12, 16, 2))
        cards.pack(fill="x")
        self.card_val = {}
        specs = [("应有", "应收", INK), ("已收", "已交", GREEN),
                 ("未交", "未交", RED), ("未识别", "未识别", AMBER)]
        for i, (key, cap, color) in enumerate(specs):
            f = ttk.Frame(cards)
            f.grid(row=0, column=i, sticky="w", padx=(0, 40))
            v = ttk.Label(f, text="—", font=(FONT, 20, "bold"), foreground=color)
            v.pack(anchor="w")
            ttk.Label(f, text=cap, font=(FONT, 9), foreground=GRAY).pack(anchor="w")
            self.card_val[key] = v

        # 按钮分三行，各按用途归类。
        # 注意：不能全塞一行 —— 中文按钮实际渲染宽度比 width 参数大不少，
        # 塞满一行会被窗口右边缘切掉（实测过），宁可多一行也别让按钮看不见。
        self.btn_start = self.btn_stop = self.btn_find = None
        rows = [
            [("开始收作业", self.start_watch, 13),
             ("停止", self.stop_watch, 8),
             ("立即扫描一次", self.scan_once, 13),
             ("找回之前的作业", self.find_history, 16),
             ("刷新统计", self.refresh, 10)],
            [("一键打包给老师", self.do_pack, 14),
             ("打开归档文件夹", self.open_archive, 14),
             ("编辑学生名单", self.edit_roster, 12),
             ("修改设置", self.edit_settings, 9),
             ("清理冗余文件", self.clean_junk, 12)],
            [("探测 QQ 接收目录", self.probe_dirs, 15),
             ("手动选择文件夹", self.pick_dir, 13)],
        ]
        for r, row in enumerate(rows):
            fr = ttk.Frame(self.root, padding=(16, 12 if r == 0 else 6, 16, 2))
            fr.pack(fill="x")
            for i, (text, cmd, w) in enumerate(row):
                b = ttk.Button(fr, text=text, command=cmd, width=w)
                b.grid(row=0, column=i, padx=(0, 8))
                if text == "开始收作业":
                    self.btn_start = b
                elif text == "停止":
                    self.btn_stop = b
                    b.configure(state="disabled")
                elif text == "找回之前的作业":
                    self.btn_find = b

        self.lbl_state = ttk.Label(self.root, text="", font=(FONT, 10),
                                   padding=(18, 10, 16, 0), foreground=GRAY)
        self.lbl_state.pack(fill="x")

        box = ttk.LabelFrame(self.root, text=" 运行日志 ", padding=8)
        box.pack(fill="both", expand=True, padx=16, pady=(10, 14))
        self.txt = scrolledtext.ScrolledText(
            box, wrap="word", height=14, font=("Consolas", 9),
            background="#fcfcfc", foreground=INK, relief="flat",
            insertbackground=INK, borderwidth=0)
        self.txt.pack(fill="both", expand=True)
        self.txt.configure(state="disabled")

    # ------------------------------------------------------------ 日志
    def _append(self, text: str):
        self.txt.configure(state="normal")
        self.txt.insert("end", text)
        if int(self.txt.index("end-1c").split(".")[0]) > 3000:
            self.txt.delete("1.0", "1000.0")
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def _welcome(self):
        self._append(f"{APP} 已启动。\n")
        self._append(f"当前作业：{self.cfg['作业名称']}\n")
        ad = C.archive_dir(self.cfg)
        self._append(f"归档目录：{ad}\n")
        if self.cfg.get("按次序分文件夹", True):
            cur = C.order_letter(self.cfg)
            sub = f"{cur}作业" if cur else C.ORDER_UNKNOWN_DIR
            self._append(f"本次存放：{ad / sub}\n"
                         f"（每次的作业自动分文件夹：第1次作业/、第2次作业/……）\n")
        targets = C.watch_targets(self.cfg)
        if targets:
            for d, strict in targets:
                tag = "混合目录·只挑本班学号" if strict else "收作业目录"
                self._append(f"监控目录：{d}   [{tag}]\n")
            self._append("\n点「开始收作业」就会一直挂着自动收。\n")
            self._append("点「找回之前的作业」可以把打开程序之前收到的旧作业全捞进来。\n\n")
        else:
            self._append("\n还没确定要监控哪个文件夹。先点「探测 QQ 接收目录」。\n\n")

    def _pump(self):
        chunks = []
        try:
            while True:
                chunks.append(self.q.get_nowait())
        except queue.Empty:
            pass
        if chunks:
            self._append("".join(chunks))

        if self._watch_done:
            self._watch_done = False
            self._set_running(False)
            self._need_refresh = True

        if self._find_done:
            self._find_done = False
            self.btn_find.configure(state="normal")
            self._need_refresh = True

        if self._pending_dirs is not None:
            found, self._pending_dirs = self._pending_dirs, None
            self._ask_dir(found)

        if self._pending_clean is not None:
            rep, self._pending_clean = self._pending_clean, None
            self._ask_clean(rep)

        if self._need_refresh:
            self._need_refresh = False
            self.refresh()

        self.root.after(150, self._pump)

    # ------------------------------------------------------------ 统计
    def refresh(self):
        try:
            self.cfg = C.load_config()
            roster = C.load_roster(self.cfg)
            summary = C.build_report(self.cfg, roster, quiet=True)
            ad = C.archive_dir(self.cfg)
            unk = ad / C.UNKNOWN_DIR
            n_unk = len([p for p in unk.iterdir() if p.is_file()]) if unk.is_dir() else 0
            n_roster = len([s for s in roster if not s.startswith("@")])

            cur = C.order_letter(self.cfg)
            self.lbl_job.configure(text=self.cfg["作业名称"])
            self.lbl_dir.configure(
                text=f"归档：{ad}    本次：{cur or '未标次序'}    "
                     f"名单：{n_roster} 人    重复策略：{self.cfg['重复策略']}")
            self.card_val["应有"].configure(text=str(summary["应有"]))
            self.card_val["已收"].configure(text=str(summary["已收"]))
            self.card_val["未交"].configure(text=str(len(summary["未交"])))
            self.card_val["未识别"].configure(text=str(n_unk))
        except Exception:
            traceback.print_exc()

    def _set_running(self, flag: bool):
        self.running = flag
        if flag:
            self.btn_start.configure(state="disabled")
            self.btn_stop.configure(state="normal")
            self.lbl_state.configure(text="● 正在收作业…… 同学发新文件会自动归档",
                                     foreground=GREEN)
        else:
            self.btn_start.configure(state="normal")
            self.btn_stop.configure(state="disabled")
            self.lbl_state.configure(text="○ 已停止（点「开始收作业」继续）",
                                     foreground=GRAY)

    # ------------------------------------------------------------ 收作业
    def start_watch(self):
        if self.running:
            return
        self.cfg = C.load_config()
        if not C.load_roster(self.cfg):
            self._append("\n[!] 还没填学生名单，认不出人、也统计不了未交。\n")
            if messagebox.askyesno(APP, "还没填「学生名单.txt」。\n\n要现在打开它填一下吗？\n"
                                        "（填好保存，再回来点「开始收作业」）"):
                self.edit_roster()
            return
        dirs = C.resolve_watch_dirs(self.cfg)
        if not dirs:
            self._append("\n[!] 还没找到 QQ 的接收文件夹。\n")
            if messagebox.askyesno(APP, "还没找到 QQ 的接收文件夹。\n\n现在自动探测一下吗？"):
                self.probe_dirs()
            return

        self.stop_event = threading.Event()
        self._watch_done = False
        self.worker = threading.Thread(target=self._watch_job,
                                       args=(self.cfg, self.stop_event), daemon=True)
        self.worker.start()
        self._set_running(True)

    def _watch_job(self, cfg, stop_event):
        try:
            C.watch_loop(cfg, stop_event)
        except Exception:
            traceback.print_exc()
        finally:
            self._watch_done = True

    def stop_watch(self):
        if self.stop_event:
            self.stop_event.set()
            self._append("\n正在停止……\n")

    def scan_once(self):
        threading.Thread(target=self._scan_job, daemon=True).start()

    def _scan_job(self):
        try:
            cfg = C.load_config()
            targets = C.watch_targets(cfg)
            if not targets:
                print("没找到可扫描的目录。先点「探测 QQ 接收目录」。")
                return
            state = C.load_state(cfg)
            roster = C.load_roster(cfg)
            loose = [d for d, strict in targets if not strict]
            print(f"开始扫描（{len(loose)} 个收作业目录 + 历史搜索）……")
            got = C.scan_dirs(loose, cfg, state, roster, source="立即扫描") if loose else []
            rep = C.search_homework(cfg, roster, state, quiet=True)
            got += rep["结果"]
            if not got:
                print("没有发现新文件。")
            else:
                ok = sum(1 for r in got if r["状态"] == "已归档")
                dup = sum(1 for r in got if r["状态"] == "重复")
                print(f"\n本次处理 {len(got)} 个：归档 {ok} 个，重复留档 {dup} 个。")
            C.build_report(cfg, roster)
        except Exception:
            traceback.print_exc()
        finally:
            self._need_refresh = True

    # ------------------------------------------------------------ 找回历史作业
    def find_history(self):
        """把「打开程序之前」收到的作业全找出来收掉。"""
        if self._finding:
            return
        self._finding = True
        self.btn_find.configure(state="disabled")
        threading.Thread(target=self._find_job, daemon=True).start()

    def _find_job(self):
        try:
            cfg = C.load_config()
            if not C.load_roster(cfg):
                print("\n[!] 还没填学生名单。")
                print("    没有名单就没法判断一个文件是不是本班同学的作业，")
                print("    所以这一趟不能跑。先点「编辑学生名单」把名单填上吧。\n")
                return
            print("\n" + "=" * 60)
            rep = C.search_homework(cfg, collect=True)
            print("=" * 60)
            if rep["目录计数"]:
                print("\n这些文件夹以后会自动盯着（只挑带本班学号的作业，")
                print("所以不会把你自己的课表、资料混进来）：")
                for d, n in sorted(rep["目录计数"].items(), key=lambda x: -x[1]):
                    print(f"   {n:>3} 份  {d}")
            else:
                print("\n这些地方都没找到还没收的作业：")
                for d in rep["扫描位置"]:
                    print(f"   - {d}")
                print("\n（如果你是刚点完「接收」，文件可能还在下载，等几秒再点一次。）")
            C.build_report(cfg, C.load_roster(cfg))
        except Exception:
            traceback.print_exc()
        finally:
            self._finding = False
            self._find_done = True

    # ------------------------------------------------------------ 打包
    def do_pack(self):
        as_zip = messagebox.askyesno(
            "打包方式",
            "要压成一个 zip 压缩包吗？\n\n"
            "「是」→ 只生成一个 .zip 文件（方便直接发 QQ / 微信）\n"
            "「否」→ 生成一个文件夹")
        threading.Thread(target=self._pack_job, args=(as_zip,), daemon=True).start()

    def _pack_job(self, as_zip: bool):
        try:
            cfg = C.load_config()
            p = C.pack_archive(cfg, as_zip=as_zip)
            print(f"\n这个可以直接交给老师：\n  {p}")
            try:
                os.startfile(str(p if p.is_dir() else p.parent))
            except Exception:
                pass
        except Exception:
            traceback.print_exc()
        finally:
            self._need_refresh = True

    # ------------------------------------------------------------ 清理冗余
    def clean_junk(self):
        """扫描并清理程序产生的冗余文件（走回收站，随时能还原）。"""
        threading.Thread(target=self._clean_job, daemon=True).start()

    def _clean_job(self):
        try:
            cfg = C.load_config()
            self._pending_clean = C.scan_junk(cfg)
        except Exception:
            traceback.print_exc()
            self._pending_clean = {C.JUNK_CACHE: [], C.JUNK_DEMO: [],
                                   C.JUNK_DUP: [], C.JUNK_PACK: [],
                                   C.JUNK_OLD_DUP: [], "重复分组": {},
                                   "可清理体积": 0, "归档目录": ""}

    def _ask_clean(self, rep):
        cache = rep.get(C.JUNK_CACHE, [])
        demo = rep.get(C.JUNK_DEMO, [])
        packs = rep.get(C.JUNK_PACK, [])
        dupes = rep.get(C.JUNK_DUP, [])
        olddup = rep.get(C.JUNK_OLD_DUP, [])
        if not (cache or demo or packs or dupes or olddup):
            messagebox.showinfo(
                APP, "没有发现冗余文件，程序目录很干净。\n\n"
                     "（作业、学生名单、统计表都不在清理范围内，永远不会被误删）")
            self._append("\n[清理冗余] 扫描完成：没有需要清理的东西。\n")
            return

        def block(title, items, extra="", note=""):
            s = f"{title} {len(items)} 项{note}\n"
            for p in items[:5]:
                s += f"    {p.name}{extra}\n"
            if len(items) > 5:
                s += f"    …… 还有 {len(items) - 5} 项\n"
            return s + "\n"

        msg = "扫描到的冗余文件：\n\n"
        if cache:
            msg += block("· 缓存垃圾（删了会自动重建）", cache)
        if demo:
            msg += block("· 演练残留（demo 造的假作业）", demo, "/")
        if packs:
            msg += block("· 旧的打包产物", packs, "/",
                         "（最新那一份会留着，你可能刚要发给老师）")
        if dupes:
            msg += block("· 归档里的重复副本（同一学号存了多份）", dupes)
        if olddup:
            msg += block("· 重复留档（_重复文件 里积压的旧版本）", olddup)

        msg += ("全部送进回收站，不是永久删除，删错了随时能还原。\n"
                "你的作业文件、学生名单、统计表一概不动。\n\n"
                "现在清理吗？")
        if not messagebox.askyesno(APP, msg):
            self._append("\n[清理冗余] 已取消，什么都没删。\n")
            return

        try:
            cfg = C.load_config()
            r = C.purge_junk(cfg, kinds=[C.JUNK_CACHE, C.JUNK_DEMO, C.JUNK_PACK])
            self._append(f"\n[清理冗余] {r['说明']}"
                         f"（释放 {C.human_size(r['体积'])}）\n")
            for p in r["清理"]:
                self._append(f"    - {p}\n")
            rest = len(dupes) + len(olddup)
            if rest:
                if messagebox.askyesno(
                        APP, f"还剩 {rest} 份「重复副本 / 重复留档」"
                             f"（同一个人存了好几版）。\n\n"
                             f"统计和打包用的都是现行版本，删掉多余的\n"
                             f"不影响任何数字，只是省点空间。\n\n"
                             f"也一起清掉吗？"):
                    r2 = C.purge_junk(cfg, kinds=[C.JUNK_DUP, C.JUNK_OLD_DUP])
                    self._append(f"[清理冗余] 重复副本/留档：{r2['说明']}"
                                 f"（释放 {C.human_size(r2['体积'])}）\n")
                else:
                    self._append("[清理冗余] 重复副本/留档保留，不动它们。\n")
        except Exception:
            traceback.print_exc()
        finally:
            self._need_refresh = True

    # ------------------------------------------------------------ 其它按钮
    def open_archive(self):
        """打开归档文件夹：优先直接打开「本次作业」那个子文件夹。"""
        cfg = C.load_config()
        ad = C.archive_dir(cfg)
        ad.mkdir(parents=True, exist_ok=True)
        cur = C.order_letter(cfg)
        if cfg.get("按次序分文件夹", True) and cur:
            sub = ad / f"{cur}作业"
            if sub.is_dir():
                os.startfile(str(sub))
                return
        os.startfile(str(ad))

    def edit_roster(self):
        cfg = C.load_config()
        p = C.roster_path(cfg)
        if not p.exists():
            p.write_text("# 一行一个：学号 姓名\n", encoding="utf-8")
        os.startfile(str(p))
        self._append(f"\n[已打开名单文件] {p}\n"
                     f"按「学号 姓名」一行一个填好，保存后回来点「立即扫描一次」即可。\n")

    def probe_dirs(self):
        threading.Thread(target=self._probe_job, daemon=True).start()

    def _probe_job(self):
        try:
            print("\n开始探测 QQ 接收目录……")
            found = C.candidate_qq_dirs()
            if not found:
                print("没找到 QQ 的接收目录。下面弹个窗口让你手动指定一个 ——")
                print("建议先打开 QQ -> 左下角「三」-> 设置 -> 文件管理 -> 打开文件夹，")
                print("把那里显示的位置选进来。\n")
                self._pending_dirs = []          # 空列表 = 触发「手动选择」
                return
            picked = C.pick_watch_candidates(found)
            for i, d in enumerate(found, 1):
                docs, total, _ = C.dir_activity(d)
                mark = "   ★ 建议选这个" if d in picked else ""
                print(f"  [{i}] {d}{mark}")
                print(f"       文档 {docs} 个 / 文件共 {total} 个"
                      + ("（只有图片缓存，不像收作业的地方）" if total and not docs else ""))
            print()
            self._pending_dirs = found
        except Exception:
            traceback.print_exc()

    def _ask_dir(self, found):
        if not found:
            self.pick_dir("自动探测没找到 QQ 的接收文件夹。\n\n"
                          "下面手动挑一个来收作业：\n"
                          "建议先打开 QQ -> 左下角「三」-> 设置 -> 文件管理 -> 打开文件夹，\n"
                          "把那里显示的位置选进来。")
            return
        if len(found) == 1:
            d = str(found[0])
            if messagebox.askyesno(APP, f"找到接收目录：\n\n{d}\n\n用它来收作业吗？\n"
                                        f"（选「否」可以自己手动挑一个）"):
                self._use_dir(d)
            else:
                self.pick_dir()
            return

        win = tk.Toplevel(self.root)
        win.title("选择 QQ 接收目录")
        win.geometry("700x360")
        win.transient(self.root)
        win.grab_set()
        ttk.Label(win, text="找到这几个可能的目录，选一个作为收作业的监控位置：",
                  padding=14, wraplength=660).pack(anchor="w")
        lb = tk.Listbox(win, font=("Consolas", 9), height=9, activestyle="none")
        for d in found:
            docs, total, _ = C.dir_activity(d)
            tag = "★" if docs else " "
            lb.insert("end", f"{tag} {d}   [文档{docs}/共{total}]")
        lb.selection_set(0)
        lb.pack(fill="both", expand=True, padx=14)
        ttk.Label(win, text="★ = 里面有文档，最可能就是它；标「只有图片缓存」的别选。",
                  padding=(14, 6), foreground=GRAY).pack(anchor="w")

        def ok():
            sel = lb.curselection()
            if sel:
                self._use_dir(str(found[sel[0]]))
            win.destroy()

        def manual():
            win.destroy()
            self.pick_dir()

        row = ttk.Frame(win, padding=14)
        row.pack(fill="x")
        ttk.Button(row, text="就用这个", command=ok).pack(side="right")
        ttk.Button(row, text="取消", command=win.destroy).pack(side="right", padx=(0, 8))
        ttk.Button(row, text="都不对，手动选一个",
                   command=manual).pack(side="left")

    def pick_dir(self, tip: str = ""):
        """手动挑一个文件夹当监控目录（自动探测失败时的兜底）。"""
        if tip:
            messagebox.showinfo(APP, tip)
        d = filedialog.askdirectory(title="选择要监控的文件夹（即 QQ 的文件接收目录）")
        if d:
            self._use_dir(d)

    def _use_dir(self, d: str):
        try:
            docs, total, _ = C.dir_activity(Path(d))
        except Exception:
            docs, total = 0, 0
        # 防手滑：选到聊天图片缓存目录会很乱
        if docs == 0 and total > 30:
            if not messagebox.askyesno(
                    APP, f"这个文件夹里有 {total} 个文件，但一个文档都没有，\n"
                         f"看着像聊天的图片缓存，不像收作业的地方。\n\n{d}\n\n"
                         f"还是要用它吗？"):
                return
        cfg = C.load_config()
        cur = list(cfg.get("监控目录") or [])
        if d in cur:
            self._append(f"\n[已经在监控列表里了] {d}\n")
            return
        cur.append(d)
        cfg["监控目录"] = cur
        C.save_config(cfg)
        self.cfg = cfg
        mixed = C.is_mixed_dir(Path(d))
        if mixed:
            self._append(f"\n[已加入监控] {d}\n"
                         f"这是「下载/桌面」这类什么文件都有的目录，\n"
                         f"程序只会挑文件名带本班学号的收，别的文件不会碰。\n")
        else:
            self._append(f"\n[已加入监控] {d}\n"
                         f"这是收作业目录，来什么收什么（认不出人的会放进 _未识别/）。\n")
        self._append(f"监控列表现在共 {len(cur)} 个：\n")
        for x in cur:
            self._append(f"   - {x}\n")
        if self.running:
            self._append("正在收作业中，已自动按新目录重新开始……\n")
            self.stop_watch()
            self.root.after(900, self._restart_watch)
        self.refresh()

    def _restart_watch(self):
        if self.running:            # 还没停干净，再等一会儿
            self.root.after(400, self._restart_watch)
            return
        self.start_watch()

    def edit_settings(self):
        cfg = C.load_config()
        win = tk.Toplevel(self.root)
        win.title("修改设置")
        win.geometry("640x460")
        win.transient(self.root)
        win.grab_set()

        body = ttk.Frame(win, padding=16)
        body.pack(fill="both", expand=True)

        ttk.Label(body, text="作业名称").grid(row=0, column=0, sticky="w", pady=6)
        v_job = tk.StringVar(value=cfg["作业名称"])
        ttk.Entry(body, textvariable=v_job, width=42).grid(row=0, column=1, sticky="w", pady=6)

        ttk.Label(body, text="归档目录").grid(row=1, column=0, sticky="w", pady=6)
        v_dir = tk.StringVar(value=cfg["归档目录"])
        ttk.Entry(body, textvariable=v_dir, width=42).grid(row=1, column=1, sticky="w", pady=6)
        ttk.Label(body, text="相对路径 = 放在程序旁边", font=(FONT, 8),
                  foreground=GRAY).grid(row=2, column=1, sticky="w")

        ttk.Label(body, text="归档方式").grid(row=3, column=0, sticky="w", pady=6)
        v_mode = tk.StringVar(value=cfg["归档方式"])
        ttk.Combobox(body, textvariable=v_mode, values=["复制", "移动"],
                     width=40, state="readonly").grid(row=3, column=1, sticky="w", pady=6)

        ttk.Label(body, text="重复策略").grid(row=4, column=0, sticky="w", pady=6)
        v_dup = tk.StringVar(value=cfg["重复策略"])
        ttk.Combobox(body, textvariable=v_dup, values=["最新", "最早", "全部保留"],
                     width=40, state="readonly").grid(row=4, column=1, sticky="w", pady=6)

        ttk.Label(body, text="命名模板").grid(row=5, column=0, sticky="w", pady=6)
        v_tpl = tk.StringVar(value=cfg["命名模板"])
        ttk.Entry(body, textvariable=v_tpl, width=42).grid(row=5, column=1, sticky="w", pady=6)
        ttk.Label(body, text="可用：{作业名称} {学号} {姓名} {后缀} {次序} {课程} {班级}",
                  font=(FONT, 8), foreground=GRAY).grid(row=6, column=1, sticky="w")

        ttk.Label(body, text="额外找作业的位置").grid(row=7, column=0, sticky="w", pady=6)
        v_scope = tk.StringVar(value="；".join(cfg.get("搜索范围") or []))
        ttk.Entry(body, textvariable=v_scope, width=42).grid(row=7, column=1, sticky="w", pady=6)
        ttk.Label(body, text="多个用「;」隔开。留空 = 自动搜 下载/桌面/文档/QQ/微信",
                  font=(FONT, 8), foreground=GRAY).grid(row=8, column=1, sticky="w")

        ttk.Label(body, text="启动时自动找回旧作业").grid(row=9, column=0, sticky="w", pady=6)
        v_auto = tk.BooleanVar(value=bool(cfg.get("启动时搜索历史作业", True)))
        ttk.Checkbutton(body, variable=v_auto,
                        text="每次点「开始收作业」时，先翻一遍旧作业").grid(
            row=9, column=1, sticky="w", pady=6)

        def save():
            new = C.load_config()
            new["作业名称"] = v_job.get().strip() or new["作业名称"]
            new["归档目录"] = v_dir.get().strip() or new["归档目录"]
            new["归档方式"] = v_mode.get()
            new["重复策略"] = v_dup.get()
            new["命名模板"] = v_tpl.get().strip() or new["命名模板"]
            new["搜索范围"] = [x.strip() for x in
                             re.split(r"[;；\n]+", v_scope.get()) if x.strip()]
            new["启动时搜索历史作业"] = bool(v_auto.get())
            C.save_config(new)
            self.cfg = new
            win.destroy()
            self.refresh()
            self._append(f"\n[设置已保存] 作业名称 = {new['作业名称']}，"
                         f"归档目录 = {new['归档目录']}\n")
            if self.running:
                messagebox.showinfo(APP, "设置已保存。\n\n"
                                         "正在收作业，新设置需要先「停止」再「开始」才生效。")

        row = ttk.Frame(win, padding=(16, 0, 16, 16))
        row.pack(fill="x")
        ttk.Button(row, text="保存", command=save).pack(side="right")
        ttk.Button(row, text="取消", command=win.destroy).pack(side="right", padx=(0, 8))

    # ------------------------------------------------------------ 关闭
    def on_close(self):
        if self.running:
            if not messagebox.askyesno(APP, "还在收作业，确定要关掉吗？"):
                return
            if self.stop_event:
                self.stop_event.set()
        self.root.destroy()


def main():
    C.setup_console()
    try:
        root = tk.Tk()
    except tk.TclError as e:
        print(f"开不了窗口：{e}")
        print("（如果你在远程桌面 / 无桌面环境里，请改用命令行版 start.bat）")
        return
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
