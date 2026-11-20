#!/usr/bin/env python3
"""
Client — Windows (müşteri tarafı)
Kullanım: UzaktanDesteK.exe --id user --pw 1234
"""
import sys
import os
import socket
import struct
import subprocess
import threading
import time
import platform
import tkinter as tk
from tkinter import messagebox, filedialog

# ─── PyInstaller path helper ───
def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

IS_WINDOWS = platform.system() == "Windows"

if IS_WINDOWS:
    FFMPEG = resource_path("ffmpeg.exe")
    ZROK = resource_path("zrok2.exe")
else:
    FFMPEG = "ffmpeg"
    ZROK = "zrok2"

VIDEO_PORT = 9000
CTRL_PORT = 9001
WIDTH, HEIGHT = 1280, 720
FPS = 15
CHUNK_SIZE = 65536

# ─── Auth ───
def check_auth(password):
    return password == "1234"


# ─── Arg parse ───
def parse_args():
    token = None
    password = None
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--id" and i + 1 < len(args):
            token = args[i + 1]
        elif a == "--pw" and i + 1 < len(args):
            password = args[i + 1]
    return token, password


# ─── Zrok ───
def start_zrok_shares(token):
    procs = []
    p1 = subprocess.Popen(
        [ZROK, "share", "private", "--backend-mode", "tcpTunnel",
         f"127.0.0.1:{VIDEO_PORT}", "--share-token", token],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    procs.append(p1)
    p2 = subprocess.Popen(
        [ZROK, "share", "private", "--backend-mode", "tcpTunnel",
         f"127.0.0.1:{CTRL_PORT}", "--share-token", f"{token}_c"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    procs.append(p2)
    time.sleep(4)
    for p in procs:
        if p.poll() is not None:
            err = p.stderr.read().decode(errors="ignore")
            raise RuntimeError(f"Zrok hatası: {err}")
    print("[OK] Zrok tünelleri aktif")
    return procs   


# ─── Video: Ekran yakala → H.264 → TCP ───
def video_sender(conn):
    import mss
    import numpy as np

    cmd = [
        FFMPEG, "-y",
        "-f", "rawvideo", "-pix_fmt", "bgra",
        "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS),
        "-i", "-",
        "-c:v", "libx264", "-preset", "ultrafast",
        "-tune", "zerolatency", "-crf", "30",
        "-pix_fmt", "yuv420p",
        "-f", "h264", "-"
    ]
    ffmpeg = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

    last_frame = None
    with mss.mss() as sct:
        monitor = sct.monitors[1]
        while True:
            frame = sct.grab(monitor)
            arr = np.array(frame)

            if arr.shape[0] != HEIGHT or arr.shape[1] != WIDTH:
                import cv2
                arr = cv2.resize(arr, (WIDTH, HEIGHT))
                arr = cv2.cvtColor(arr, cv2.COLOR_BGRA2BGRA)

            if last_frame is not None:
                diff = np.abs(arr.astype(np.int16) - last_frame.astype(np.int16))
                if diff.mean() < 1.5:
                    time.sleep(0.05)
                    continue

            last_frame = arr
            try:
                ffmpeg.stdin.write(arr.tobytes())
            except BrokenPipeError:
                break
            time.sleep(1.0 / FPS)

    try:
        ffmpeg.stdin.close()
        ffmpeg.wait(timeout=5)
    except:
        pass


# ─── Control Server (input + chat + file) ───
def control_server(conn, gui_ref):
    if IS_WINDOWS:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        INPUT_KEYBOARD = 1
        KEYEVENTF_KEYUP = 0x0002
        MOUSEEVENTF_MOVE = 0x0001
        MOUSEEVENTF_LEFTDOWN = 0x0002
        MOUSEEVENTF_LEFTUP = 0x0004
        MOUSEEVENTF_RIGHTDOWN = 0x0008
        MOUSEEVENTF_RIGHTUP = 0x0010
        MOUSEEVENTF_WHEEL = 0x0800

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [
                ("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]
        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]
        class _U(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]
        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("u", _U)]

        def do_mouse_move(x, y):
            inp = INPUT(type=0)
            inp.u.mi.dx = x
            inp.u.mi.dy = y
            inp.u.mi.dwFlags = MOUSEEVENTF_MOVE
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))

        def do_mouse_click(btn):
            flags = {1: MOUSEEVENTF_LEFTDOWN, 2: MOUSEEVENTF_LEFTUP,
                     3: MOUSEEVENTF_RIGHTDOWN, 4: MOUSEEVENTF_RIGHTUP}
            inp = INPUT(type=0)
            inp.u.mi.dwFlags = flags.get(btn, 0)
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))

        def do_key(vk, is_up):
            inp = INPUT(type=INPUT_KEYBOARD)
            inp.u.ki.wVk = vk
            inp.u.ki.dwFlags = KEYEVENTF_KEYUP if is_up else 0
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))

        def do_scroll(amount):
            inp = INPUT(type=0)
            inp.u.mi.mouseData = amount
            inp.u.mi.dwFlags = MOUSEEVENTF_WHEEL
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
    else:
        def do_mouse_move(x, y):
            subprocess.run(["xdotool", "mousemove", str(x), str(y)], capture_output=True)
        def do_mouse_click(btn):
            btn_map = {1: "1", 2: "1", 3: "3", 4: "3"}
            subprocess.run(["xdotool", "click", btn_map.get(btn, "1")], capture_output=True)
        def do_key(vk, is_up):
            subprocess.run(["xdotool", "key", str(vk)], capture_output=True)
        def do_scroll(amount):
            btn = "4" if amount > 0 else "5"
            subprocess.run(["xdotool", "click", btn], capture_output=True)

    file_state = {"active": False, "path": None, "f": None, "received": 0, "total": 0}

    while True:
        try:
            hdr = conn.recv(4)
            if len(hdr) < 4:
                break
            msg_len = struct.unpack(">I", hdr)[0]
            if msg_len == 0 or msg_len > 10 * 1024 * 1024:
                break

            payload = b""
            while len(payload) < msg_len:
                chunk = conn.recv(min(msg_len - len(payload), CHUNK_SIZE))
                if not chunk:
                    break
                payload += chunk
            if len(payload) < msg_len:
                break

            msg_type = payload[0]
            data = payload[1:]

            if msg_type == 1:
                x, y = struct.unpack(">HH", data[:4])
                do_mouse_move(x, y)

            elif msg_type == 2:
                btn = data[0]
                if btn <= 4:
                    do_mouse_click(btn)
                else:
                    do_scroll(120 if btn == 5 else -120)

            elif msg_type == 3:
                vk = data[0]
                is_up = data[1]
                do_key(vk, is_up)

            elif msg_type == 10:
                msg = data.decode("utf-8", errors="ignore")
                gui_ref["chat_append"](f"[Destek] {msg}\n")

            elif msg_type == 20:
                fname_len = struct.unpack(">H", data[:2])[0]
                fname = data[2:2 + fname_len].decode("utf-8")
                fsize = struct.unpack(">Q", data[2 + fname_len:10 + fname_len])[0]
                save_dir = os.path.join(os.path.expanduser("~"), "Downloads")
                os.makedirs(save_dir, exist_ok=True)
                fpath = os.path.join(save_dir, fname)
                file_state["active"] = True
                file_state["path"] = fpath
                file_state["f"] = open(fpath, "wb")
                file_state["received"] = 0
                file_state["total"] = fsize
                gui_ref["status_set"](f"Dosya alınıyor: {fname}")

            elif msg_type == 21:
                if file_state["active"]:
                    file_state["f"].write(data)
                    file_state["received"] += len(data)
                    pct = file_state["received"] / max(file_state["total"], 1) * 100
                    gui_ref["status_set"](f"Dosya: {pct:.0f}%")

            elif msg_type == 22:
                if file_state["active"]:
                    file_state["f"].close()
                    file_state["active"] = False
                    gui_ref["status_set"]("Dosya alındı")

        except ConnectionResetError:
            break
        except Exception as e:
            print(f"[Control] Hata: {e}")
            break

    print("[Control] Bağlantı kapandı")


# ─── File Send ───
def send_file(sock, filepath, gui_ref):
    try:
        fname = os.path.basename(filepath)
        fsize = os.path.getsize(filepath)
        header = struct.pack(">BH", 20, len(fname)) + fname.encode("utf-8") + struct.pack(">Q", fsize)
        sock.sendall(struct.pack(">I", len(header)) + header)

        with open(filepath, "rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                sock.sendall(struct.pack(">I", 1 + len(chunk)) + struct.pack(">B", 21) + chunk)

        sock.sendall(struct.pack(">I", 1) + struct.pack(">B", 22))
        gui_ref["status_set"](f"Gönderildi: {fname}")
    except Exception as e:
        gui_ref["status_set"](f"Dosya hatası: {e}")


# ─── GUI ───
def main():
    auto_token, auto_pw = parse_args()

    root = tk.Tk()
    root.title("Uzaktan Destek")
    root.geometry("420x340")
    root.resizable(False, False)

    tk.Label(root, text="ID:", font=("Arial", 10)).pack(anchor="w", padx=15, pady=(10, 0))
    id_entry = tk.Entry(root, width=30, font=("Consolas", 11))
    id_entry.pack(padx=15, pady=2)
    if auto_token:
        id_entry.insert(0, auto_token)

    tk.Label(root, text="Şifre:", font=("Arial", 10)).pack(anchor="w", padx=15, pady=(8, 0))
    pw_entry = tk.Entry(root, width=30, font=("Consolas", 11), show="*")
    pw_entry.pack(padx=15, pady=2)
    if auto_pw:
        pw_entry.insert(0, auto_pw)

    status = tk.Label(root, text="Hazır", font=("Arial", 9), fg="gray")
    status.pack(pady=5)

    chat_frame = tk.Frame(root)
    chat_frame.pack(fill="both", expand=True, padx=15, pady=5)
    chat_display = tk.Text(chat_frame, height=5, font=("Consolas", 9), state="disabled")
    chat_display.pack(side="left", fill="both", expand=True)
    chat_input = tk.Entry(chat_frame, font=("Arial", 9))
    chat_input.pack(side="bottom", fill="x", pady=(2, 0))

    btn_frame = tk.Frame(root)
    btn_frame.pack(fill="x", padx=15, pady=5)

    ctrl_sock = [None]
    running = [False]
    zrok_procs = []

    def chat_append(text):
        root.after(0, lambda: (chat_display.config(state="normal"),
                               chat_display.insert("end", text),
                               chat_display.see("end"),
                               chat_display.config(state="disabled")))

    def status_set(text):
        root.after(0, lambda: status.config(text=text))

    def on_chat_send(event=None):
        msg = chat_input.get().strip()
        if msg and ctrl_sock[0]:
            data = struct.pack(">B", 10) + msg.encode("utf-8")
            try:
                ctrl_sock[0].sendall(struct.pack(">I", len(data)) + data)
            except:
                pass
            chat_input.delete(0, tk.END)
            chat_append(f"[Ben] {msg}\n")

    chat_input.bind("<Return>", on_chat_send)

    def on_send_file():
        if not ctrl_sock[0]:
            messagebox.showwarning("Uyarı", "Önce bağlanın.")
            return
        filepath = filedialog.askopenfilename()
        if filepath:
            threading.Thread(target=send_file, args=(ctrl_sock[0], filepath,
                              {"status_set": status_set}), daemon=True).start()

    def on_start():
        token = id_entry.get().strip()
        password = pw_entry.get().strip()
        if not token or not password:
            messagebox.showerror("Hata", "ID ve şifre girin.")
            return
        if not check_auth(password):
            messagebox.showerror("Hata", "Geçersiz şifre.")
            return

        running[0] = True
        status_set("Bağlanılıyor...")
        root.update()

        try:
            video_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            video_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            video_server.bind(("127.0.0.1", VIDEO_PORT))
            video_server.listen(1)

            ctrl_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            ctrl_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            ctrl_server.bind(("127.0.0.1", CTRL_PORT))
            ctrl_server.listen(1)

            status_set("Zrok tünelleri açılıyor...")
            root.update()
            zrok_procs.extend(start_zrok_shares(token))
            status_set("Bağlı. Bekleniyor...")
            root.update()

            print("[*] Video bağlantısı bekleniyor...")
            video_conn, _ = video_server.accept()
            print("[OK] Video bağlı")

            print("[*] Control bağlantısı bekleniyor...")
            ctrl_conn, _ = ctrl_server.accept()
            print("[OK] Control bağlı")
            ctrl_sock[0] = ctrl_conn
            status_set("Aktif — Bağlantı var")

            gui_ref = {"chat_append": chat_append, "status_set": status_set}
            t_video = threading.Thread(target=video_sender, args=(video_conn,), daemon=True)
            t_ctrl = threading.Thread(target=control_server, args=(ctrl_conn, gui_ref), daemon=True)
            t_video.start()
            t_ctrl.start()

            while running[0]:
                time.sleep(0.5)
                root.update()

        except Exception as e:
            status_set(f"Hata: {e}")
            root.update()
            messagebox.showerror("Hata", str(e))
        finally:
            running[0] = False
            for p in zrok_procs:
                try:
                    p.terminate()
                except:
                    pass

    def on_close():
        running[0] = False
        for p in zrok_procs:
            try:
                p.terminate()
            except:
                pass
        root.destroy()

    tk.Button(btn_frame, text="Başlat", command=on_start,
              font=("Arial", 10, "bold"), bg="#2d6da3", fg="white",
              padx=15, pady=3).pack(side="left", padx=5)
    tk.Button(btn_frame, text="Dosya Gönder", command=on_send_file,
              font=("Arial", 9), padx=10, pady=3).pack(side="left", padx=5)
    tk.Button(btn_frame, text="Kapat", command=on_close,
              font=("Arial", 9), bg="#c0392b", fg="white",
              padx=15, pady=3).pack(side="right", padx=5)

    root.protocol("WM_DELETE_WINDOW", on_close)

    if auto_token and auto_pw:
        root.after(500, on_start)

    root.mainloop()


if __name__ == "__main__":
    main()   
