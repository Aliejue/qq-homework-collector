# -*- coding: utf-8 -*-
"""
作业收集器 —— QQ 机器人模式（进阶，全自动）
================================================================
原理：
    同学把作业发到群 / 私聊  ->  NapCat 上报事件到本程序  ->
    本程序拉取文件 -> 自动认人改名 -> 归档 -> 回复同学「已收」

需要先装好 NapCat（OneBot 11 实现），并在 NapCat 里配置：
    「网络配置 -> HTTP 服务器」开启（默认端口 3000，供本程序调用接口）
    「网络配置 -> HTTP 上报」地址填  http://127.0.0.1:8765/onebot
    （如果 NapCat 和本程序不在同一台机器，把 127.0.0.1 换成对应 IP）

启动： python qq_bot.py
     或双击 start_bot.bat

⚠ 注意：这类第三方客户端有被腾讯风控的风险，
   建议用「备用 QQ 号」，主号请优先使用 run.py 的文件夹监控模式。
================================================================
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import collector as C  # noqa: E402

CFG = C.load_config()
BOT = CFG["机器人"]
ROSTER = C.load_roster(CFG)
STATE = C.load_state(CFG)
STATE_LOCK = threading.Lock()
TMP_DIR = C.archive_dir(CFG) / "_临时下载"

# 文件扩展名 -> 判断是不是作业文件
WANT_EXT = [e.lower() for e in CFG["文件后缀"]]


# ------------------------------------------------------------ 调用 NapCat 接口
def call_api(action: str, params: dict, timeout: int = 20) -> dict:
    """调用 OneBot 11 接口。"""
    url = BOT["OneBot地址"].rstrip("/") + "/" + action
    data = json.dumps(params).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "User-Agent": "hw-collector/1.0"},
    )
    if BOT.get("OneBot令牌"):
        req.add_header("Authorization", "Bearer " + BOT["OneBot令牌"])
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        return {"status": "failed", "message": f"HTTP {e.code}"}
    except Exception as e:  # noqa: BLE001
        return {"status": "failed", "message": str(e)}


def reply(kind: str, target_id, text: str) -> None:
    """回复消息。"""
    if not BOT.get("收到后回复"):
        return
    action = "send_group_msg" if kind == "group" else "send_private_msg"
    key = "group_id" if kind == "group" else "user_id"
    call_api(action, {key: target_id, "message": text})


# ------------------------------------------------------------ 取文件下载地址
def resolve_download_url(kind: str, group_id, user_id,
                         file_id: str, busid: int) -> tuple[str, str]:
    """
    拿到文件的下载地址或本地路径。
    返回 (url_or_path, 说明)，url_or_path 为空表示失败。
    """
    if kind == "group":
        # NapCat 不同版本参数名有 group_id / group 两种，都试一遍
        for params in ({"group_id": group_id, "file_id": file_id, "busid": busid},
                       {"group": str(group_id), "file_id": file_id, "busid": busid}):
            r = call_api("get_group_file_url", params)
            if r.get("status") == "ok" and r.get("data", {}).get("url"):
                return r["data"]["url"], "get_group_file_url"
    else:
        r = call_api("get_private_file_url", {"user_id": user_id, "file_id": file_id})
        if r.get("status") == "ok" and r.get("data", {}).get("url"):
            return r["data"]["url"], "get_private_file_url"

    # 兜底：get_file 有时直接返回本地路径或 url
    r = call_api("get_file", {"file_id": file_id})
    d = r.get("data") or {}
    if d.get("url"):
        return d["url"], "get_file.url"
    if d.get("file") and os.path.exists(str(d["file"])):
        return str(d["file"]), "get_file.local"
    return "", "全部取址方式都失败了"


def fetch_file(url_or_path: str, filename: str) -> Path | None:
    """把文件下载到临时目录，返回本地路径。"""
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    dst = TMP_DIR / C.safe_filename(f"{C.stamp()}_{filename}")
    try:
        if url_or_path.startswith("http"):
            req = urllib.request.Request(
                url_or_path, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=120) as resp, \
                    open(dst, "wb") as f:
                shutil.copyfileobj(resp, f, 1024 * 256)
        else:
            shutil.copy2(url_or_path, dst)
    except Exception as e:  # noqa: BLE001
        C.log(CFG, f"  ! 下载失败：{e}")
        return None
    return dst


# ------------------------------------------------------------ 核心处理
def handle_file(kind: str, group_id, user_id, file_id: str,
                file_name: str, busid: int = 102) -> None:
    """处理一个收到的作业文件。"""
    who = f"群{group_id}" if kind == "group" else f"好友{user_id}"
    C.log(CFG, f"收到文件：{file_name}  来自 {who}  (file_id={file_id})")

    if Path(file_name).suffix.lower() not in WANT_EXT:
        C.log(CFG, f"  跳过：不是作业文件格式（{Path(file_name).suffix}）")
        return

    url, how = resolve_download_url(kind, group_id, user_id, file_id, busid)
    if not url:
        C.log(CFG, f"  ! 取不到下载地址：{how}")
        reply(kind, group_id or user_id, "文件下载失败了，麻烦重新发一次～")
        return

    local = fetch_file(url, file_name)
    if not local:
        reply(kind, group_id or user_id, "文件下载失败了，麻烦重新发一次～")
        return

    with STATE_LOCK:
        res = C.process_file(local, CFG, STATE, ROSTER, source=who)
        C.save_state(CFG, STATE)

    # 临时文件清掉（归档方式是复制，所以可以安全删除）
    try:
        local.unlink()
    except OSError:
        pass

    if res["状态"] == "已归档":
        if res["学号"] or res["姓名"]:
            text = BOT["回复模板"].format(
                作业名称=CFG["作业名称"],
                文件名=Path(res["目标"]).name,
                学号=res["学号"] or "?",
                姓名=res["姓名"] or "?",
            )
        else:
            text = (f"作业收到了，但文件名里没看出你是谁 😅\n"
                    f"下次麻烦命名成「{CFG['作业名称']}_学号_姓名」再发给我哦～")
        reply(kind, group_id or user_id, text)
        C.build_report(CFG, ROSTER, quiet=True)


def parse_event(event: dict) -> list[tuple]:
    """从 OneBot 事件里解析出所有 (kind, group_id, user_id, file_id, name, busid)。"""
    out: list[tuple] = []
    post_type = event.get("post_type")

    # 1) 群文件上传通知
    if post_type == "notice" and event.get("notice_type") == "group_upload":
        f = event.get("file") or {}
        if f.get("id") or f.get("file_id"):
            out.append(("group", event.get("group_id"), None,
                        str(f.get("id") or f.get("file_id")),
                        f.get("name") or "未命名", int(f.get("busid") or 102)))

    # 2) 私聊离线文件通知
    elif post_type == "notice" and event.get("notice_type") == "offline_file":
        f = event.get("file") or {}
        out.append(("private", None, event.get("user_id"),
                    str(f.get("id") or f.get("file_id") or ""),
                    f.get("name") or "未命名", 0))
        if not f.get("id") and f.get("url"):
            # 直接给了 url，稍后用特殊标记处理
            out[-1] = ("private-url", None, event.get("user_id"),
                       f["url"], f.get("name") or "未命名", 0)

    # 3) 聊天消息里带 file 段
    elif post_type == "message":
        segs = event.get("message")
        if isinstance(segs, str):  # 极少数实现会直接给 CQ 码字符串
            segs = []
        for seg in segs or []:
            if seg.get("type") != "file":
                continue
            d = seg.get("data") or {}
            fid = str(d.get("file_id") or d.get("file") or "")
            if not fid:
                continue
            kind = "group" if event.get("message_type") == "group" else "private"
            out.append((kind, event.get("group_id"), event.get("user_id"),
                        fid, str(d.get("file") or d.get("name") or "未命名"),
                        int(d.get("busid") or 102)))
    return out


def allowed(kind: str, group_id, user_id) -> bool:
    if kind == "group":
        wl = [str(x) for x in BOT.get("只处理这些群") or []]
        return (group_id is None) or (not wl) or (str(group_id) in wl)
    wl = [str(x) for x in BOT.get("只处理这些好友") or []]
    return (user_id is None) or (not wl) or (str(user_id) in wl)


def on_event(event: dict) -> None:
    try:
        for item in parse_event(event):
            kind, group_id, user_id, file_id, name, busid = item
            if not allowed("group" if kind.startswith("group") else "private",
                           group_id, user_id):
                C.log(CFG, f"不在白名单，忽略：{name}")
                continue
            if kind == "private-url":
                local = fetch_file(file_id, name)
                if local:
                    with STATE_LOCK:
                        C.process_file(local, CFG, STATE, ROSTER, source="好友文件")
                        C.save_state(CFG, STATE)
                    try:
                        local.unlink()
                    except OSError:
                        pass
                continue
            handle_file(kind, group_id, user_id, file_id, name, busid)
    except Exception as e:  # noqa: BLE001
        C.log(CFG, f"! 处理事件出错：{e}\n{json.dumps(event, ensure_ascii=False)[:400]}")


# ------------------------------------------------------------ HTTP 回调服务
class Handler(BaseHTTPRequestHandler):
    server_version = "hw-collector"

    def do_POST(self):  # noqa: N802
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
        except (ValueError, OSError):
            body = b""
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()
        if self.path.rstrip("/") not in ("/onebot", ""):
            return
        try:
            event = json.loads(body.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            return
        threading.Thread(target=on_event, args=(event,), daemon=True).start()

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("作业收集机器人正在运行 ✅\n".encode("utf-8"))

    def log_message(self, *args):  # 别刷屏
        pass


def main() -> None:
    C.setup_console()
    host, port = BOT["监听地址"], int(BOT["监听端口"])
    C.archive_dir(CFG)
    C.log(CFG, "=" * 62)
    C.log(CFG, "作业收集机器人启动")
    C.log(CFG, f"作业名称：{CFG['作业名称']}")
    C.log(CFG, f"归档目录：{C.archive_dir(CFG)}")
    C.log(CFG, f"本机回调地址（填进 NapCat 的 HTTP 上报）：http://{host}:{port}/onebot")
    C.log(CFG, f"OneBot 接口地址：{BOT['OneBot地址']}")

    info = call_api("get_login_info", {})
    d = info.get("data") or {}
    if info.get("status") == "ok":
        C.log(CFG, f"✅ 已连上 OneBot，登录账号：{d.get('nickname')}（{d.get('user_id')}）")
    else:
        C.log(CFG, f"⚠ 连不上 OneBot 接口（{info.get('message')}）。"
                   f"请确认 NapCat 已启动、端口 {BOT['OneBot地址']} 正确。")
    C.log(CFG, "等待同学们发作业…… 按 Ctrl+C 停止。")
    C.log(CFG, "=" * 62)

    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        C.log(CFG, "已停止机器人。")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
