#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LANChat v5 — 局域网聊天工具（纯 P2P，无独立服务端）

v5 新增：
- 左侧工具箱：一键启动内置 CS1.6（随 exe 嵌入，首次运行自动解压到程序同目录）
- 左侧工具箱：一键关闭极域电子教室（taskkill /F /IM 进程名）
"""
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import uuid
import tkinter as tk
from tkinter import messagebox, scrolledtext

# ============================== 全局配置 ==============================
APP_MAGIC = "LANCHAT-V5"
DISCOVERY_PORT = 37020
HEARTBEAT_INTERVAL = 2.5
PEER_TIMEOUT = 15.0
GROUP_CONV = "__group__"
MAX_HISTORY = 1000
FONT = "Microsoft YaHei UI"
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# 内置 CS1.6：打包时随 exe 一起嵌入，首次运行自动解压到程序同目录
CS_DIR_NAME = "CS1_6"
CS_EXE_NAMES = ("cs1.6.exe", "hl.exe")
CS_STAMP_NAME = ".extracted"

# 左下提示最多显示的行数：封顶后不会挤压用户列表，也不会越出窗口底部
HINT_MAX_LINES = 8

# 要关闭的极域进程名（主进程 + 常见守护进程），逐个用 taskkill /F /IM 进程名 结束
TARGET_PROCESSES = ["StudentMain.exe", "TopDomained.exe", "TopDomino.exe"]


def base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = base_dir()
DATA_DIR = os.environ.get("LANCHAT_DATA_DIR") or os.path.join(BASE_DIR, "data")
HISTORY_DIR = os.path.join(DATA_DIR, "history")
os.makedirs(HISTORY_DIR, exist_ok=True)

# 嵌入资源根目录：onefile 打包时为 PyInstaller 的临时解包目录，源码运行时为脚本目录
BUNDLE_DIR = getattr(sys, "_MEIPASS", BASE_DIR)
CS_DIR = os.path.join(BASE_DIR, CS_DIR_NAME)
CS_BUNDLE_DIR = os.path.join(BUNDLE_DIR, CS_DIR_NAME)


def cs_exe_path():
    """返回内置 CS1.6 启动程序的完整路径；未解压或缺失时返回 None"""
    for name in CS_EXE_NAMES:
        p = os.path.join(CS_DIR, name)
        if os.path.isfile(p):
            return p
    return None


def cs_ready():
    return os.path.isfile(os.path.join(CS_DIR, CS_STAMP_NAME)) and \
        cs_exe_path() is not None


def _copy_file(src, dst):
    """复制文件并保留时间戳/只读属性；Windows 走系统 API，失败或其它平台走分块复制"""
    if os.path.exists(dst):
        try:
            os.chmod(dst, 0o666)          # 清掉只读，允许覆盖上次残留的文件
        except OSError:
            pass
    done = False
    if sys.platform == "win32":
        try:
            import ctypes
            done = bool(ctypes.windll.kernel32.CopyFileW(src, dst, False))
        except Exception:
            done = False
    if not done:
        with open(src, "rb") as fs, open(dst, "wb") as fd:
            while True:
                chunk = fs.read(1 << 20)
                if not chunk:
                    break
                fd.write(chunk)
    try:
        st = os.stat(src)
        os.chmod(dst, st.st_mode)
        os.utime(dst, (st.st_atime, st.st_mtime))
    except OSError:
        pass


def extract_cs(progress=None):
    """把嵌入的 CS1_6 解压到程序同目录，返回启动程序路径；失败抛 OSError"""
    if os.path.abspath(CS_BUNDLE_DIR) == os.path.abspath(CS_DIR):
        return cs_exe_path()
    if cs_ready():
        return cs_exe_path()
    if not os.path.isdir(CS_BUNDLE_DIR):
        return None

    stamp = os.path.join(CS_DIR, CS_STAMP_NAME)
    try:
        os.remove(stamp)
    except OSError:
        pass

    items = []
    for dirpath, _dirnames, filenames in os.walk(CS_BUNDLE_DIR):
        for name in filenames:
            src = os.path.join(dirpath, name)
            items.append((src, os.path.join(CS_DIR, os.path.relpath(src, CS_BUNDLE_DIR))))

    os.makedirs(CS_DIR, exist_ok=True)
    for i, (src, dst) in enumerate(items, 1):
        parent = os.path.dirname(dst)
        if parent:
            os.makedirs(parent, exist_ok=True)
        _copy_file(src, dst)
        if progress is not None and (i % 200 == 0 or i == len(items)):
            progress(i, len(items))

    # 全部复制成功后才写标记，中断时下次启动会重新解压
    with open(stamp, "w", encoding="utf-8") as f:
        f.write(f"{APP_MAGIC}\n{len(items)}\n")
    return cs_exe_path()


# ============================== 网络层 ==============================
class ChatNode:
    def __init__(self, nick, event_queue):
        self.uid = uuid.uuid4().hex[:10]
        self.base_nick = nick
        self.nick = nick
        self.nick_fixed = False
        self.q = event_queue
        self.running = True

        self.peers = {}
        self.peers_lock = threading.Lock()

        self.out_conns = {}
        self.in_conns = {}
        self.conns_lock = threading.Lock()
        self.tcp_port = 0
        self.udp_sock = None
        self.tcp_sock = None
        self.broadcast_targets = []
        self.local_ip = "127.0.0.1"
        self._last_sig = None

        self._seen_group = {}
        self._seen_group_lock = threading.Lock()
        self._seen_group_ttl = 120.0
        self._seen_group_max = 2000

        self._compute_broadcast_targets()

    def start(self):
        if not self._start_tcp():
            self.q.put(("notice", "TCP 监听启动失败，将无法接收私聊"))
        self._start_udp()
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        threading.Thread(target=self._cleanup_loop, daemon=True).start()

    def stop(self):
        if not self.running:
            return
        self.running = False
        try:
            self._broadcast_udp({"magic": APP_MAGIC, "type": "bye",
                                 "uid": self.uid, "timestamp": time.time()})
        except Exception:
            pass
        for s in (self.udp_sock, self.tcp_sock):
            try:
                if s:
                    s.close()
            except OSError:
                pass
        with self.conns_lock:
            for s in list(self.out_conns.values()) + list(self.in_conns.values()):
                try:
                    s.close()
                except OSError:
                    pass
            self.out_conns.clear()
            self.in_conns.clear()

    def _compute_broadcast_targets(self):
        ips = set()
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.5)
            s.connect(("8.8.8.8", 80))
            ips.add(s.getsockname()[0])
            s.close()
        except OSError:
            pass
        try:
            for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
                ips.add(ip)
        except OSError:
            pass
        ips = {ip for ip in ips if not ip.startswith("127.")}
        if ips:
            self.local_ip = sorted(ips)[0]
        targets = {"255.255.255.255"}
        for ip in ips:
            p = ip.split(".")
            if len(p) == 4:
                targets.add(".".join(p[:3]) + ".255")
                targets.add(".".join(p[:2]) + ".255.255")
        self.broadcast_targets = sorted(targets)

    def _start_tcp(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("", 0))
            s.listen(64)
            self.tcp_sock = s
            self.tcp_port = s.getsockname()[1]
            threading.Thread(target=self._accept_loop, args=(s,), daemon=True).start()
            return True
        except OSError:
            return False

    def _accept_loop(self, srv):
        while self.running:
            try:
                conn, addr = srv.accept()
            except OSError:
                break
            threading.Thread(target=self._conn_reader,
                             args=(conn, addr), daemon=True).start()

    def _conn_reader(self, conn, addr):
        buf = b""
        try:
            while self.running:
                try:
                    chunk = conn.recv(8192)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line.decode("utf-8"))
                    except Exception:
                        continue
                    try:
                        self._handle_tcp_message(msg, conn, addr)
                    except Exception:
                        pass
        finally:
            self._drop_conn(conn)

    def _drop_conn(self, conn):
        with self.conns_lock:
            for d in (self.out_conns, self.in_conns):
                for k in [k for k, v in d.items() if v is conn]:
                    del d[k]
        try:
            conn.close()
        except OSError:
            pass

    def _send_line(self, sock, obj):
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        sock.sendall(data)

    def _handle_tcp_message(self, msg, conn, addr):
        mtype = msg.get("type")
        sender = msg.get("from")
        if sender and sender != self.uid:
            with self.conns_lock:
                self.in_conns[sender] = conn
            need_pub = False
            with self.peers_lock:
                now = time.time()
                if sender in self.peers:
                    p = self.peers[sender]
                    was_online = p.get("online", False)
                    p["last_seen"] = now
                    p["online"] = True
                    if isinstance(msg.get("from_port"), int):
                        p["tcp_port"] = msg["from_port"]
                        p["ip"] = msg.get("from_ip") or addr[0]
                        if msg.get("from_nick"):
                            p["nick"] = str(msg["from_nick"])[:32]
                    need_pub = not was_online
                elif isinstance(msg.get("from_port"), int):
                    self.peers[sender] = {
                        "nick": str(msg.get("from_nick") or "未知")[:32],
                        "ip": msg.get("from_ip") or addr[0],
                        "tcp_port": msg["from_port"],
                        "last_seen": now,
                        "online": True,
                    }
                    need_pub = True
            if need_pub:
                self._publish_peers()

        if mtype == "private_msg":
            self.q.put(("private", msg))
            ack = {"type": "ack", "from": self.uid, "to": sender,
                   "messageId": msg.get("messageId"), "timestamp": time.time()}
            try:
                self._send_line(conn, ack)
            except OSError:
                pass
        elif mtype == "ack":
            self.q.put(("ack", msg))

    def _connect_to(self, uid, ip, port):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3.0)
        try:
            s.connect((ip, port))
        except OSError:
            s.close()
            raise
        s.settimeout(None)
        with self.conns_lock:
            old = self.out_conns.get(uid)
            self.out_conns[uid] = s
        if old and old is not s:
            try:
                old.close()
            except OSError:
                pass
        threading.Thread(target=self._conn_reader, args=(s, (ip, port)), daemon=True).start()
        return s

    def _get_send_socket(self, uid, peer=None):
        with self.conns_lock:
            s = self.out_conns.get(uid)
            if s:
                return s
            s = self.in_conns.get(uid)
            if s:
                return s
        if peer is None:
            with self.peers_lock:
                peer = self.peers.get(uid)
        if not peer:
            return None
        try:
            return self._connect_to(uid, peer["ip"], peer["tcp_port"])
        except OSError:
            return None

    def _start_udp(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            s.bind(("", DISCOVERY_PORT))
        except OSError:
            try:
                s.bind(("", 0))
            except OSError:
                s.close()
                self.q.put(("notice", "UDP 端口绑定失败，无法进行局域网发现"))
                return
        s.settimeout(1.0)
        self.udp_sock = s
        threading.Thread(target=self._udp_loop, daemon=True).start()

    def _udp_loop(self):
        while self.running:
            sock = self.udp_sock
            if sock is None:
                break
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                msg = json.loads(data.decode("utf-8"))
            except Exception:
                continue
            if msg.get("magic") != APP_MAGIC:
                continue
            self._handle_udp(msg, addr)

    def _handle_udp(self, msg, addr):
        mtype = msg.get("type")
        if mtype == "hello":
            self._handle_hello(msg, addr)
        elif mtype == "bye":
            uid = msg.get("uid")
            if uid and uid != self.uid:
                changed = False
                with self.peers_lock:
                    if uid in self.peers and self.peers[uid]["online"]:
                        self.peers[uid]["online"] = False
                        changed = True
                if changed:
                    self._publish_peers()
        elif mtype == "group_msg":
            if msg.get("from") == self.uid:
                return
            mid = msg.get("messageId")
            if mid:
                now = time.time()
                with self._seen_group_lock:
                    if len(self._seen_group) > self._seen_group_max // 2:
                        expired = [k for k, v in self._seen_group.items()
                                   if now - v > self._seen_group_ttl]
                        for k in expired:
                            del self._seen_group[k]
                    if mid in self._seen_group:
                        return
                    self._seen_group[mid] = now
                    if len(self._seen_group) > self._seen_group_max:
                        oldest = min(self._seen_group.items(), key=lambda x: x[1])[0]
                        del self._seen_group[oldest]
            self.q.put(("group", msg))

    def _handle_hello(self, msg, addr):
        uid = msg.get("uid")
        port = msg.get("tcp_port")
        if not uid or uid == self.uid or not isinstance(port, int) or port <= 0:
            return
        nick = str(msg.get("nick") or "未知")[:32]

        with self.peers_lock:
            now = time.time()
            if uid in self.peers:
                self.peers[uid].update({
                    "nick": nick, "ip": addr[0], "tcp_port": port,
                    "last_seen": now, "online": True,
                })
            else:
                old_uid = None
                for ouid, p in self.peers.items():
                    if p["nick"] == nick and not p["online"]:
                        old_uid = ouid
                        break
                if old_uid:
                    self.peers.pop(old_uid, None)
                self.peers[uid] = {
                    "nick": nick, "ip": addr[0], "tcp_port": port,
                    "last_seen": now, "online": True,
                }

        self._check_nick_conflict(uid, nick)
        self._publish_peers()

    def _check_nick_conflict(self, peer_uid, peer_nick):
        if self.nick_fixed or peer_nick != self.nick:
            return
        if self.uid < peer_uid:
            return
        self.nick = f"{self.base_nick}({self.uid[:4]})"
        self.nick_fixed = True
        self.q.put(("self_nick", self.nick))
        self.q.put(("notice", f"检测到重名，你的昵称已自动改为「{self.nick}」"))
        self._send_hello()

    def _publish_peers(self):
        with self.peers_lock:
            snap = {u: dict(p) for u, p in self.peers.items()}
        sig = tuple(sorted(
            (u, p["nick"], p["ip"], p["tcp_port"], p.get("online", False))
            for u, p in snap.items()))
        if sig != self._last_sig:
            self._last_sig = sig
            self.q.put(("peers", snap))

    def _send_hello(self):
        self._broadcast_udp({
            "magic": APP_MAGIC, "type": "hello",
            "uid": self.uid, "nick": self.nick,
            "tcp_port": self.tcp_port, "timestamp": time.time(),
        })

    def _broadcast_udp(self, obj):
        if not self.udp_sock:
            return
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        for tgt in self.broadcast_targets:
            try:
                self.udp_sock.sendto(data, (tgt, DISCOVERY_PORT))
            except OSError:
                pass

    def _heartbeat_loop(self):
        for _ in range(3):
            if not self.running:
                return
            self._send_hello()
            time.sleep(0.4)
        last = time.time()
        while self.running:
            time.sleep(0.5)
            if time.time() - last >= HEARTBEAT_INTERVAL:
                self._send_hello()
                last = time.time()

    def _cleanup_loop(self):
        while self.running:
            time.sleep(1.0)
            now = time.time()
            changed = False
            with self.peers_lock:
                for p in self.peers.values():
                    if p["online"] and now - p["last_seen"] > PEER_TIMEOUT:
                        p["online"] = False
                        changed = True
            if changed:
                self._publish_peers()

    def send_private(self, peer_uid, content, message_id):
        with self.peers_lock:
            peer = self.peers.get(peer_uid)
            if not peer:
                return False, "对方不在线"
            if not peer.get("online"):
                return False, "对方已离线"
            peer_snap = dict(peer)
        msg = {
            "type": "private_msg",
            "from": self.uid, "from_nick": self.nick,
            "from_ip": self.local_ip, "from_port": self.tcp_port,
            "to": peer_uid, "content": content,
            "timestamp": time.time(), "messageId": message_id,
        }
        for attempt in (1, 2):
            sock = self._get_send_socket(peer_uid, peer_snap)
            if sock is None:
                return False, "无法连接到对方（可能被防火墙拦截）"
            try:
                self._send_line(sock, msg)
                return True, None
            except OSError:
                self._drop_conn(sock)
                if attempt == 2:
                    return False, "消息发送失败"
        return False, "消息发送失败"

    def send_group(self, content):
        if len(content.encode("utf-8")) > 3000:
            content = content[:1000] + "…（消息过长已截断）"
        self._broadcast_udp({
            "magic": APP_MAGIC, "type": "group_msg",
            "from": self.uid, "from_nick": self.nick,
            "content": content, "timestamp": time.time(),
            "messageId": uuid.uuid4().hex,
        })


# ============================== 界面层 ==============================
class ChatApp:
    def __init__(self, root, nick):
        self.root = root
        self.q = queue.Queue()
        self.node = ChatNode(nick, self.q)
        self.current_conv = GROUP_CONV
        self.histories = {}
        self.pending = {}
        self.peers = {}

        self._pending_flash = {}
        self._flash_running = False
        self._conv_labels = {}
        self._topmost_after_id = None

        self._build_ui()
        self.node.start()
        self.root.title(f"局域网聊天 — {self.node.nick}")
        self.root.after(80, self._poll_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(12000, self._check_discovery_hint)
        self.root.after(200, self._watch_focus_for_topmost)

    # ============================ UI 构建 ============================
    def _build_ui(self):
        self.root.geometry("1080x680")
        self.root.configure(bg="#f5f5f5")
        self.root.minsize(860, 540)

        # ---------- 左侧 ----------
        left = tk.Frame(self.root, width=230, bg="#ededed")
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        top = tk.Frame(left, bg="#dfdfdf")
        top.pack(fill="x")
        self.my_label = tk.Label(top, text=f"我：{self.node.nick}", anchor="w",
                                 font=(FONT, 11, "bold"), bg="#dfdfdf",
                                 fg="#333", padx=12, pady=10)
        self.my_label.pack(fill="x")

        # ---------- 工具箱（新增） ----------
        toolbox = tk.Frame(left, bg="#ededed")
        toolbox.pack(fill="x", padx=8, pady=(8, 4))

        tk.Label(toolbox, text="工具箱", anchor="w", font=(FONT, 9, "bold"),
                 bg="#ededed", fg="#666").pack(fill="x", pady=(0, 4))

        self.cs_btn = tk.Button(toolbox, text="启动 CS", command=self._launch_cs,
                                font=(FONT, 10, "bold"), bg="#4a90e2", fg="white",
                                activebackground="#3a7ac9", activeforeground="white",
                                relief="flat", cursor="hand2", pady=5)
        self.cs_btn.pack(fill="x", pady=2)

        self.cs_path_label = tk.Label(toolbox, text=self._cs_path_hint(), anchor="w",
                                      font=(FONT, 8), bg="#ededed", fg="#888",
                                      wraplength=214, justify="left")
        self.cs_path_label.pack(fill="x", pady=(0, 4))

        self.kill_btn = tk.Button(toolbox, text="关闭极域", command=self._kill_studentmain,
                                  font=(FONT, 10, "bold"), bg="#e94b3c", fg="white",
                                  activebackground="#c93a2c", activeforeground="white",
                                  relief="flat", cursor="hand2", pady=5)
        self.kill_btn.pack(fill="x", pady=2)

        self.hint_sep = tk.Frame(left, height=1, bg="#d5d5d5")
        self.hint_sep.pack(fill="x", padx=8, pady=(6, 4))

        # ---------- 左下提示（先于列表 pack：空间不足时收缩列表，保证提示完整可见） ----------
        self.hint_label = tk.Label(left, text="", anchor="w", justify="left",
                                   font=(FONT, 9), bg="#ededed", fg="#c0392b",
                                   wraplength=210, padx=10, pady=6)
        self.hint_label.pack(fill="x", side="bottom")

        # ---------- 会话列表 ----------
        wrap = tk.Frame(left, bg="#ededed")
        wrap.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(wrap, bg="#ededed", highlightthickness=0)
        vsb = tk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.list_frame = tk.Frame(self.canvas, bg="#ededed")
        self.canvas.create_window((0, 0), window=self.list_frame, anchor="nw", tags="inner")
        self.list_frame.bind("<Configure>",
                             lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfig("inner", width=e.width))

        # ---------- 右侧 ----------
        right = tk.Frame(self.root, bg="#f7f7f7")
        right.pack(side="left", fill="both", expand=True)

        self.title_label = tk.Label(right, text="群聊（所有人）", anchor="w",
                                    font=(FONT, 12, "bold"), bg="#ffffff",
                                    fg="#222", padx=16, pady=10)
        self.title_label.pack(side="top", fill="x")

        self.bottom = tk.Frame(right, bg="#ffffff", height=88)
        self.bottom.pack(side="bottom", fill="x")
        self.bottom.pack_propagate(False)

        self.send_btn = tk.Button(self.bottom, text="发送", command=self.send_current,
                                  font=(FONT, 11), bg="#07c160", fg="white",
                                  activebackground="#06a94f", relief="flat",
                                  width=8, cursor="hand2")
        self.send_btn.pack(side="right", padx=(0, 12), pady=8)

        self.input_box = tk.Text(self.bottom, height=2, font=(FONT, 11),
                                 relief="flat", bg="#ffffff", fg="#000000",
                                 padx=10, pady=8, wrap="word",
                                 insertbackground="black")
        self.input_box.pack(side="left", fill="both", expand=True, padx=(10, 6), pady=8)
        self.input_box.bind("<Return>", self._on_enter)
        self.input_box.bind("<Shift-Return>", self._on_shift_enter)

        self.chat_area = scrolledtext.ScrolledText(
            right, state="disabled", wrap="word", font=(FONT, 11),
            bg="#f7f7f7", relief="flat", padx=16, pady=10,
            spacing1=2, spacing3=3, height=6)
        self.chat_area.pack(side="top", fill="both", expand=True)
        self.chat_area.tag_configure("meta", foreground="#8a8a8a", font=(FONT, 9))
        self.chat_area.tag_configure("me", foreground="#1a7f37", font=(FONT, 11))
        self.chat_area.tag_configure("peer", foreground="#1f4e9c", font=(FONT, 11))
        self.chat_area.tag_configure("sys", foreground="#b45309", font=(FONT, 9))

        self._refresh_user_list()
        self.root.after(200, lambda: self.input_box.focus_set())

    # ============================ 工具箱功能 ============================
    def _cs_path_hint(self):
        p = cs_exe_path()
        if p:
            return "内置 CS1.6：" + os.path.basename(p)
        return "内置 CS1.6 未解压（重启程序会自动解压）"

    def _launch_cs(self):
        if sys.platform != "win32":
            messagebox.showinfo("提示", "该功能仅支持 Windows", parent=self.root)
            return
        path = cs_exe_path()
        if not path:
            messagebox.showerror("启动 CS 失败",
                                 "未找到内置 CS1.6 文件，请重新运行程序以解压。",
                                 parent=self.root)
            return
        try:
            exe_name = os.path.basename(path).lower()
            if exe_name == "hl.exe":
                args = [path, "-game", "cstrike"]
            else:
                args = [path]
            subprocess.Popen(args, cwd=os.path.dirname(path))
            self._show_notice(f"CS 已启动：{os.path.basename(path)}")
        except Exception as e:
            messagebox.showerror("启动 CS 失败", str(e), parent=self.root)

    def _kill_studentmain(self):
        if sys.platform != "win32":
            messagebox.showinfo("提示", "该功能仅支持 Windows", parent=self.root)
            return
        killed = []
        not_found = []
        failed = []
        for exe in TARGET_PROCESSES:
            try:
                r = subprocess.run(
                    ["taskkill", "/F", "/IM", exe],
                    capture_output=True, timeout=10,
                    creationflags=_CREATE_NO_WINDOW)
                if r.returncode == 0:
                    killed.append(exe)
                elif r.returncode == 128:
                    not_found.append(exe)
                else:
                    failed.append(exe)
            except Exception:
                failed.append(exe)
        if killed:
            self._show_notice(f"✅ 已关闭：{', '.join(killed)}")
        elif not_found and not failed:
            self._show_notice("极域未在运行（或已被关闭）")
        else:
            msg = ("无法关闭以下进程，可能权限不足：\n  " + ", ".join(failed) +
                   "\n\n建议：\n"
                   "1. 右键本程序 → 以管理员身份运行\n"
                   "2. 或按 Ctrl+Shift+Esc 打开任务管理器手动结束 StudentMain.exe")
            self._show_notice("⚠ 关闭极域失败，可能需要管理员权限")
            messagebox.showwarning("关闭极域失败", msg, parent=self.root)

    # ============================ 会话列表 ============================
    def _refresh_user_list(self):
        for w in self.list_frame.winfo_children():
            w.destroy()
        self._conv_labels = {}

        self._add_item(GROUP_CONV, "群聊（所有人）", "👥", online=True)
        tk.Frame(self.list_frame, height=1, bg="#d5d5d5").pack(fill="x", padx=10, pady=4)

        online_peers = sorted([p for p in self.peers.values() if p.get("online")],
                              key=lambda p: p["nick"])
        offline_peers = sorted([p for p in self.peers.values() if not p.get("online")],
                               key=lambda p: p["nick"])

        if not online_peers and not offline_peers:
            tk.Label(self.list_frame, text="（暂无其他用户）", anchor="w",
                     font=(FONT, 9), bg="#ededed", fg="#999",
                     padx=12, pady=8).pack(fill="x")
            return

        for p in online_peers:
            self._add_item(p["nick"], p["nick"], "●", online=True)

        if offline_peers:
            if online_peers:
                tk.Frame(self.list_frame, height=1, bg="#d5d5d5").pack(
                    fill="x", padx=10, pady=3)
            for p in offline_peers:
                self._add_item(p["nick"], p["nick"], "○", online=False)

    def _add_item(self, conv_id, text, prefix, online=True):
        active = (conv_id == self.current_conv)
        bg = "#d3e6ff" if active else "#ededed"
        fg = "#222" if online else "#999"
        label_text = f"{prefix} {text}"
        if not online and conv_id != GROUP_CONV:
            label_text += "（离线）"
        lbl = tk.Label(self.list_frame, text=label_text, anchor="w",
                       bg=bg, fg=fg, font=(FONT, 11),
                       padx=12, pady=9, cursor="hand2")
        lbl.pack(fill="x", padx=4, pady=1)
        self._conv_labels[conv_id] = lbl
        setattr(lbl, "_is_online", online)
        lbl.bind("<Button-1>", lambda e, c=conv_id: self.open_conversation(c))
        if online:
            lbl.bind("<Enter>", lambda e, l=lbl, c=conv_id:
                     l.configure(bg="#e2e2e2") if c != self.current_conv else None)
            lbl.bind("<Leave>", lambda e, l=lbl, c=conv_id:
                     l.configure(bg="#d3e6ff" if c == self.current_conv else "#ededed"))

    def open_conversation(self, conv_id):
        if conv_id in self._pending_flash:
            self._pending_flash.pop(conv_id, None)
            self._apply_flash_color(conv_id, on=False)

        self.current_conv = conv_id
        if conv_id == GROUP_CONV:
            self.title_label.configure(text="群聊（所有人）")
        else:
            peer = None
            for p in self.peers.values():
                if p["nick"] == conv_id:
                    peer = p
                    break
            if peer is None:
                self.title_label.configure(text=conv_id + "（离线）")
            elif peer.get("online"):
                self.title_label.configure(text=conv_id)
            else:
                self.title_label.configure(text=conv_id + "（离线）")
        self._refresh_user_list()
        self._render_all()
        self.input_box.focus_set()

    # ============================ 历史记录 ============================
    def _conv_file(self, conv_id):
        safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in conv_id)
        return os.path.join(HISTORY_DIR, safe + ".json")

    def load_history(self, conv_id):
        if conv_id in self.histories:
            return self.histories[conv_id]
        data = []
        try:
            with open(self._conv_file(conv_id), "r", encoding="utf-8") as f:
                obj = json.load(f)
            if isinstance(obj, list):
                data = obj
        except (OSError, ValueError):
            data = []
        self.histories[conv_id] = data
        return data

    def save_history(self, conv_id):
        data = self.histories.get(conv_id, [])[-MAX_HISTORY:]
        self.histories[conv_id] = data
        try:
            with open(self._conv_file(conv_id), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except OSError:
            pass

    def add_message(self, conv_id, rec):
        hist = self.load_history(conv_id)
        hist.append(rec)
        if len(hist) > MAX_HISTORY:
            del hist[:len(hist) - MAX_HISTORY]
        self.save_history(conv_id)
        if conv_id == self.current_conv:
            self._render_rec(rec)
            self.chat_area.see("end")

    # ============================ 渲染 ============================
    def _append_text(self, text, *tags):
        self.chat_area.configure(state="normal")
        self.chat_area.insert("end", text, tags if tags else ())
        self.chat_area.configure(state="disabled")

    def _render_rec(self, rec):
        direction = rec.get("direction")
        if direction == "sys":
            self._append_text("      " + rec.get("text", "") + "\n\n", "sys")
            return
        ts = rec.get("ts") or time.time()
        tstr = time.strftime("%H:%M:%S", time.localtime(ts))
        tag = "me" if direction == "out" else "peer"
        self._append_text(f"{rec.get('nick', '')}   {tstr}\n", "meta")
        self._append_text(rec.get("text", "") + "\n", tag)
        mid = rec.get("mid")
        if mid and direction == "out":
            self._append_text(rec.get("status", "") + "\n", f"st_{mid}", "meta")
        self._append_text("\n")

    def _render_all(self):
        self.chat_area.configure(state="normal")
        self.chat_area.delete("1.0", "end")
        self.chat_area.configure(state="disabled")
        for rec in self.load_history(self.current_conv):
            self._render_rec(rec)
        self.chat_area.see("end")

    def _set_status(self, mid, status_text, conv_id):
        for rec in self.histories.get(conv_id, []):
            if rec.get("mid") == mid:
                rec["status"] = status_text
        self.save_history(conv_id)
        if conv_id != self.current_conv:
            return
        r = self.chat_area.tag_ranges(f"st_{mid}")
        if not r:
            return
        self.chat_area.configure(state="normal")
        self.chat_area.delete(r[0], r[1])
        self.chat_area.insert(r[0], status_text, (f"st_{mid}", "meta"))
        self.chat_area.configure(state="disabled")

    # ============================ 发送 ============================
    def _on_enter(self, event):
        self.send_current()
        return "break"

    def _on_shift_enter(self, event):
        self.input_box.insert("insert", "\n")
        return "break"

    def _uid_for_nick(self, nick):
        for uid, p in self.peers.items():
            if p["nick"] == nick:
                return uid
        return None

    def send_current(self):
        text = self.input_box.get("1.0", "end").strip()
        if not text:
            return
        self.input_box.delete("1.0", "end")
        conv = self.current_conv

        if conv == GROUP_CONV:
            self.node.send_group(text)
            self.add_message(conv, {"nick": "我", "text": text, "direction": "out",
                                    "ts": time.time(), "status": ""})
            return

        uid = self._uid_for_nick(conv)
        if not uid:
            self.add_message(conv, {"nick": "系统", "text": "对方不在线，消息未发送",
                                    "direction": "sys", "ts": time.time()})
            return

        peer = self.peers.get(uid)
        if not peer or not peer.get("online"):
            self.add_message(conv, {"nick": "系统", "text": "对方已离线，消息未发送",
                                    "direction": "sys", "ts": time.time()})
            return

        mid = uuid.uuid4().hex
        rec = {"nick": "我", "text": text, "direction": "out",
               "ts": time.time(), "mid": mid, "status": "⏳ 发送中"}
        self.pending[mid] = conv
        self.add_message(conv, rec)
        ok, err = self.node.send_private(uid, text, mid)
        if ok:
            return
        self.pending.pop(mid, None)
        self._set_status(mid, "✗ " + (err or "发送失败"), conv)

    # ============================ 事件轮询 ============================
    def _poll_events(self):
        try:
            while True:
                ev, payload = self.q.get_nowait()
                if ev == "peers":
                    self._on_peers(payload)
                elif ev == "private":
                    self._on_private(payload)
                elif ev == "group":
                    self._on_group(payload)
                elif ev == "ack":
                    self._on_ack(payload)
                elif ev == "self_nick":
                    self.my_label.configure(text=f"我：{payload}")
                    self.root.title(f"局域网聊天 — {payload}")
                elif ev == "notice":
                    self._show_notice(payload)
        except queue.Empty:
            pass
        self.root.after(80, self._poll_events)

    def _on_peers(self, snapshot):
        self.peers = snapshot
        self._refresh_user_list()
        if self.current_conv != GROUP_CONV:
            peer = None
            for p in snapshot.values():
                if p["nick"] == self.current_conv:
                    peer = p
                    break
            if peer is None:
                self.title_label.configure(text=self.current_conv + "（离线）")
            elif peer.get("online"):
                self.title_label.configure(text=self.current_conv)
            else:
                self.title_label.configure(text=self.current_conv + "（离线）")
        if snapshot:
            self._set_hint("")

    def _on_private(self, msg):
        nick = str(msg.get("from_nick") or "未知")[:32]
        rec = {"nick": nick, "text": msg.get("content", ""),
               "direction": "in", "ts": msg.get("timestamp", time.time())}
        self.add_message(nick, rec)

        active = self._is_window_active()
        is_current = (nick == self.current_conv)

        if is_current:
            if not active:
                self._bring_to_front()
        else:
            self._show_notice(f"收到来自「{nick}」的新消息")
            if not active:
                self._bring_to_front()
                self.open_conversation(nick)
            else:
                self._flash_conv(nick)

    def _on_group(self, msg):
        nick = str(msg.get("from_nick") or "未知")[:32]
        rec = {"nick": nick, "text": msg.get("content", ""),
               "direction": "in", "ts": msg.get("timestamp", time.time())}
        self.add_message(GROUP_CONV, rec)

        active = self._is_window_active()
        is_current = (self.current_conv == GROUP_CONV)

        if is_current:
            if not active:
                self._bring_to_front()
        else:
            self._show_notice(f"群聊有新消息（来自「{nick}」）")
            if not active:
                self._bring_to_front()
                self.open_conversation(GROUP_CONV)
            else:
                self._flash_conv(GROUP_CONV)

    def _on_ack(self, msg):
        mid = msg.get("messageId")
        conv = self.pending.pop(mid, None)
        if not conv:
            return
        self._set_status(mid, "✓ 已送达", conv)

    def _show_notice(self, text):
        self._set_hint(text)

    def _set_hint(self, text):
        """设置左下提示：超过 HINT_MAX_LINES 行时封顶，避免挤压用户列表或越出窗口底部"""
        lbl = self.hint_label
        lbl.configure(text=text, height=0)
        if not text:
            return
        try:
            self.root.update_idletasks()
            line = int(self.root.tk.call("font", "metrics", lbl.cget("font"), "-linespace"))
            pad = 2 * int(lbl.cget("pady"))
        except (tk.TclError, ValueError):
            return
        if line > 0 and lbl.winfo_reqheight() > HINT_MAX_LINES * line + pad:
            lbl.configure(height=HINT_MAX_LINES)

    def _check_discovery_hint(self):
        if not self.peers:
            self._set_hint("未发现其他用户。\n"
                           "请检查：\n"
                           "1. 是否与对方在同一局域网\n"
                           "2. Windows 防火墙是否允许本程序通信\n"
                           "3. 是否有多台机器在同一网段")

    # ============================ 焦点 / 置顶 / 闪烁 ============================
    def _get_hwnd(self):
        if sys.platform != "win32":
            return 0
        try:
            import ctypes
        except Exception:
            return 0
        for getter in (lambda: int(self.root.frame(), 16),
                       lambda: int(self.root.winfo_id())):
            try:
                h = getter()
                if h:
                    top = ctypes.windll.user32.GetAncestor(h, 2)
                    return top or h
            except Exception:
                continue
        return 0

    def _force_foreground(self):
        if sys.platform != "win32":
            try:
                self.root.deiconify()
                self.root.lift()
                self.root.focus_force()
                return True
            except Exception:
                return False

        import ctypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        hwnd = self._get_hwnd()
        if not hwnd:
            return False
        if user32.GetForegroundWindow() == hwnd:
            return True

        try:
            user32.ShowWindow(hwnd, 9)
        except Exception:
            pass

        fg_hwnd = user32.GetForegroundWindow()
        fg_thread = user32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0
        cur_thread = kernel32.GetCurrentThreadId()

        attached = False
        if fg_thread and fg_thread != cur_thread:
            try:
                attached = bool(user32.AttachThreadInput(cur_thread, fg_thread, True))
            except Exception:
                attached = False

        try:
            user32.SetForegroundWindow(hwnd)
            user32.BringWindowToTop(hwnd)
            try:
                self.root.focus_force()
            except Exception:
                pass
        finally:
            if attached:
                try:
                    user32.AttachThreadInput(cur_thread, fg_thread, False)
                except Exception:
                    pass

        return user32.GetForegroundWindow() == hwnd

    def _bring_to_front(self):
        if self._force_foreground():
            return
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.focus_force()
        except Exception:
            pass
        if self._topmost_after_id is not None:
            try:
                self.root.after_cancel(self._topmost_after_id)
            except Exception:
                pass
        self._topmost_after_id = self.root.after(3000, self._clear_topmost)

    def _clear_topmost(self):
        self._topmost_after_id = None
        try:
            self.root.attributes("-topmost", False)
        except Exception:
            pass

    def _watch_focus_for_topmost(self):
        if self._topmost_after_id is not None and not self._is_window_active():
            try:
                self.root.after_cancel(self._topmost_after_id)
            except Exception:
                pass
            self._topmost_after_id = None
            try:
                self.root.attributes("-topmost", False)
            except Exception:
                pass
        self.root.after(200, self._watch_focus_for_topmost)

    def _is_window_active(self):
        if sys.platform == "win32":
            try:
                import ctypes
                fg = ctypes.windll.user32.GetForegroundWindow()
                hwnd = self._get_hwnd()
                return hwnd != 0 and fg == hwnd
            except Exception:
                pass
        try:
            focused = self.root.focus_displayof()
            if focused is None:
                return False
            try:
                return focused.winfo_toplevel() is self.root
            except Exception:
                return False
        except Exception:
            return True

    def _flash_conv(self, conv_id):
        self._pending_flash[conv_id] = 8
        if not self._flash_running:
            self._flash_running = True
            self.root.after(0, self._flash_tick)

    def _flash_tick(self):
        to_remove = []
        for conv_id, remain in list(self._pending_flash.items()):
            if remain <= 0:
                to_remove.append(conv_id)
                self._apply_flash_color(conv_id, on=False)
                continue
            self._apply_flash_color(conv_id, on=(remain % 2 == 0))
            self._pending_flash[conv_id] = remain - 1
        for c in to_remove:
            del self._pending_flash[c]
        if self._pending_flash:
            self.root.after(250, self._flash_tick)
        else:
            self._flash_running = False

    def _apply_flash_color(self, conv_id, on):
        lbl = self._conv_labels.get(conv_id)
        if lbl is None:
            return
        try:
            if on:
                lbl.configure(bg="#ff9800", fg="#ffffff")
            else:
                if conv_id == self.current_conv:
                    lbl.configure(bg="#d3e6ff", fg="#222")
                else:
                    is_online = getattr(lbl, "_is_online", True)
                    lbl.configure(bg="#ededed", fg="#222" if is_online else "#999")
        except tk.TclError:
            pass

    def _on_close(self):
        try:
            self.node.stop()
        finally:
            self.root.destroy()


# ============================== 启动入口 ==============================
def default_nick():
    try:
        h = socket.gethostname()
        return h[:12] if h else "同学" + uuid.uuid4().hex[:3]
    except Exception:
        return "同学" + uuid.uuid4().hex[:3]


def ask_nickname_in_root(root):
    for w in root.winfo_children():
        w.destroy()
    root.title("局域网聊天工具")
    root.geometry("400x220")
    root.resizable(False, False)
    root.configure(bg="#f7f7f7")

    state: dict = {"nick": None}
    done_var = tk.BooleanVar(root, False)

    tk.Label(root, text="请输入你的昵称", font=(FONT, 13, "bold"),
             bg="#f7f7f7", fg="#222").pack(pady=(26, 12))

    var = tk.StringVar(value=default_nick())
    entry = tk.Entry(root, textvariable=var, font=(FONT, 12),
                     justify="center", relief="flat", bg="#ffffff")
    entry.pack(padx=40, fill="x", ipady=7)
    entry.focus_set()
    entry.select_range(0, "end")

    def submit(event=None):
        n = var.get().strip()
        if not n:
            messagebox.showwarning("提示", "昵称不能为空", parent=root)
            return
        state["nick"] = n[:16]
        done_var.set(True)

    tk.Button(root, text="进入聊天", command=submit, font=(FONT, 11),
              bg="#07c160", fg="white", relief="flat", width=14,
              cursor="hand2").pack(pady=18)
    root.bind("<Return>", submit)

    def on_close():
        state["nick"] = None
        done_var.set(True)
    root.protocol("WM_DELETE_WINDOW", on_close)

    root.update_idletasks()
    root.deiconify()
    root.lift()
    root.focus_force()

    try:
        root.wait_variable(done_var)
    except tk.TclError:
        pass
    root.unbind("<Return>")
    return state["nick"]


def prepare_bundled_cs(root):
    """启动时把嵌入的 CS1.6 解压到程序同目录；已解压则直接复用"""
    if os.path.abspath(CS_BUNDLE_DIR) == os.path.abspath(CS_DIR) or cs_ready():
        return cs_exe_path()

    label = None
    try:
        for w in root.winfo_children():
            w.destroy()
        root.title("局域网聊天工具")
        root.geometry("440x150")
        root.resizable(False, False)
        root.configure(bg="#f7f7f7")
        label = tk.Label(root, text="首次运行，正在解压内置 CS1.6…",
                         font=(FONT, 12), bg="#f7f7f7", fg="#222")
        label.pack(expand=True)
        root.deiconify()
        root.lift()
        root.update()
    except tk.TclError:
        label = None

    def on_progress(done, total):
        if label is None:
            return
        try:
            label.configure(text=f"正在解压内置 CS1.6：{done}/{total}")
            root.update()
        except tk.TclError:
            pass

    try:
        return extract_cs(on_progress)
    except OSError as e:
        try:
            messagebox.showerror(
                "解压 CS1.6 失败",
                f"{e}\n\n请把程序放到有写入权限的目录后重新运行。",
                parent=root)
        except tk.TclError:
            pass
        return None


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    try:
        root = tk.Tk()
    except Exception as e:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, f"Tk 初始化失败：{e}", "LANChat", 0x10)
        except Exception:
            print(f"Tk 初始化失败：{e}")
        return

    cs_path = prepare_bundled_cs(root)

    try:
        nick = ask_nickname_in_root(root)
        if not nick:
            root.destroy()
            return

        for w in root.winfo_children():
            w.destroy()
        root.resizable(True, True)
        root.protocol("WM_DELETE_WINDOW", root.destroy)

        app = ChatApp(root, nick)
        if cs_path is None:
            app._show_notice("⚠ 内置 CS1.6 未解压，无法启动 CS")
        root.deiconify()
        root.lift()
        root.mainloop()
    except Exception:
        import traceback
        tb = traceback.format_exc()
        try:
            messagebox.showerror("启动失败", tb[:1500])
        except Exception:
            print(tb)
        raise


if __name__ == "__main__":
    main()