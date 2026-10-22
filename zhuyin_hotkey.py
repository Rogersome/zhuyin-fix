"""zhuyin-fix 快捷鍵工具（Windows）

在任何程式裡選取亂碼，按 Ctrl+Shift+Z，旁邊會跳出候選字，選好後直接替換原本的文字。

    python zhuyin_hotkey.py      # 開著這個視窗就會生效，關掉即結束
    pythonw zhuyin_hotkey.py     # 不顯示主控台視窗，在背景執行

候選視窗：數字鍵 1-9 或 ↑↓ + Enter 選字，Esc 取消，也可以直接用滑鼠點。
選過的字會記住（存在同資料夾的 user_dict.json），和命令列工具共用。

只用 Python 標準函式庫：ctypes 呼叫 Windows API，tkinter 畫候選視窗。
"""
from __future__ import annotations

import queue
import sys
import threading
import time
from pathlib import Path

from zhuyin_fix import Candidate, ZhuyinFixer

HOTKEY_LABEL = "Ctrl+Shift+Z"


# ---------------------------------------------------------------- 與平台無關的部分


def build_options(engine: ZhuyinFixer, text: str, k: int = 9) -> list[tuple[str, Candidate | None]]:
    """決定候選視窗要顯示什麼。

    - 選取的整段都是亂碼：列出前 k 個候選（容許漏打 / 多打一鍵）。
    - 中文和亂碼混在一起（例如「我朋友叫eji ru8 u/3」）：只轉換亂碼的部分，給一個結果。
    - 看不出亂碼：回傳空清單。
    """
    text = text.rstrip("\r\n")
    if not text.strip():
        return []
    cands = engine.decode(text, k=k)
    if cands:
        return [(c.text, c) for c in cands]
    fixed, report = engine.fix_text(text, threshold=0.5)
    if report and fixed != text:
        return [(fixed, None)]
    return []


# ---------------------------------------------------------------- Windows API


def _init_win32():
    import ctypes
    from ctypes import wintypes as wt

    global ctypes_mod, wt_mod, user32, kernel32
    ctypes_mod, wt_mod = ctypes, wt
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
    user32.RegisterHotKey.restype = wt.BOOL
    user32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
    user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
    user32.GetMessageW.restype = wt.BOOL
    user32.GetForegroundWindow.restype = wt.HWND
    user32.SetForegroundWindow.argtypes = [wt.HWND]
    user32.BringWindowToTop.argtypes = [wt.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    user32.GetWindowThreadProcessId.restype = wt.DWORD
    user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.keybd_event.argtypes = [wt.BYTE, wt.BYTE, wt.DWORD, ctypes.c_size_t]
    user32.GetClipboardSequenceNumber.restype = wt.DWORD
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
    user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
    user32.GetAncestor.restype = wt.HWND
    kernel32.GetCurrentThreadId.restype = wt.DWORD

    # 高 DPI 螢幕：讓 tkinter 座標和滑鼠座標一致
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
    except (OSError, AttributeError):
        user32.SetProcessDPIAware()


MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x0002, 0x0004, 0x4000
WM_HOTKEY = 0x0312
VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN = 0x10, 0x11, 0x12, 0x5B, 0x5C
VK_C, VK_V, VK_Z = 0x43, 0x56, 0x5A
KEYEVENTF_KEYUP = 0x0002
GA_ROOT = 2


def hotkey_thread(q: queue.Queue) -> None:
    """RegisterHotKey 要在同一個執行緒跑訊息迴圈，所以獨立一個執行緒。"""
    if not user32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, VK_Z):
        q.put(("error", f"無法註冊 {HOTKEY_LABEL}，可能已被其他程式占用。"))
        return
    msg = wt_mod.MSG()
    while user32.GetMessageW(ctypes_mod.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_HOTKEY:
            q.put(("hotkey", None))


def wait_keys_released(timeout: float = 1.5) -> None:
    """等使用者放開 Ctrl/Shift/Z，否則送出的 Ctrl+C 會變成 Ctrl+Shift+C。"""
    keys = (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN, VK_Z)
    end = time.time() + timeout
    while time.time() < end:
        if not any(user32.GetAsyncKeyState(k) & 0x8000 for k in keys):
            return
        time.sleep(0.01)


def send_ctrl(vk: int) -> None:
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


def force_foreground(hwnd) -> None:
    """Windows 會阻擋背景程式搶焦點；暫時掛到目前前景視窗的輸入佇列上就能切換。"""
    if not hwnd:
        return
    fg = user32.GetForegroundWindow()
    me = kernel32.GetCurrentThreadId()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(fg_tid and fg_tid != me and user32.AttachThreadInput(me, fg_tid, True))
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    if attached:
        user32.AttachThreadInput(me, fg_tid, False)


def cursor_pos() -> tuple[int, int]:
    pt = wt_mod.POINT()
    user32.GetCursorPos(ctypes_mod.byref(pt))
    return pt.x, pt.y


# ---------------------------------------------------------------- 主程式


class App:
    FONT = ("Microsoft JhengHei UI", 14)
    HINT_FONT = ("Microsoft JhengHei UI", 9)
    BG, FG, SEL_BG, SEL_FG, HINT = "#ffffff", "#1f1f1f", "#2563eb", "#ffffff", "#6b7280"

    def __init__(self):
        import tkinter as tk
        self.tk = tk
        self.engine = ZhuyinFixer(user_dict_path=Path(__file__).with_name("user_dict.json"))
        self.root = tk.Tk()
        self.root.withdraw()
        self.q: queue.Queue = queue.Queue()
        threading.Thread(target=hotkey_thread, args=(self.q,), daemon=True).start()
        self.popup = None
        self.busy = False
        self.root.after(30, self.poll)

    # ---- 事件迴圈 ----

    def poll(self) -> None:
        try:
            while True:
                kind, data = self.q.get_nowait()
                if kind == "error":
                    print(data)
                    self.root.destroy()
                    return
                if kind == "hotkey" and not self.busy:
                    self.on_hotkey()
        except queue.Empty:
            pass
        self.root.after(30, self.poll)

    # ---- 剪貼簿 ----

    def get_clipboard(self) -> str | None:
        try:
            return self.root.clipboard_get()
        except self.tk.TclError:          # 空的，或內容不是文字（例如圖片）
            return None

    def set_clipboard(self, text: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()

    def copy_selection(self) -> str | None:
        seq = user32.GetClipboardSequenceNumber()
        wait_keys_released()
        send_ctrl(VK_C)
        end = time.time() + 0.6
        while time.time() < end:
            self.root.update()
            if user32.GetClipboardSequenceNumber() != seq:
                time.sleep(0.03)
                return self.get_clipboard()
            time.sleep(0.02)
        return None                         # 剪貼簿沒變：沒有選取任何文字

    def restore_clipboard(self) -> None:
        if self.old_clip is not None:
            self.set_clipboard(self.old_clip)
        self.busy = False

    # ---- 流程 ----

    def on_hotkey(self) -> None:
        self.busy = True
        self.target = user32.GetForegroundWindow()
        self.old_clip = self.get_clipboard()
        text = self.copy_selection()
        if not text or not text.strip():
            self.toast("請先選取要轉換的亂碼")
            return
        self.keys = text.rstrip("\r\n")
        self.options = build_options(self.engine, text)
        if not self.options:
            self.toast("看不出是注音亂碼")
            return
        self.show_popup()

    def choose(self, idx: int) -> None:
        text, cand = self.options[idx]
        self.close_popup()
        self.set_clipboard(text)
        force_foreground(self.target)
        time.sleep(0.08)
        send_ctrl(VK_V)
        if cand is not None:
            self.engine.select(self.keys, cand)   # 記住使用者的選擇
        self.root.after(400, self.restore_clipboard)  # 等對方程式貼上後再還原剪貼簿

    def cancel(self) -> None:
        self.close_popup()
        force_foreground(self.target)
        self.restore_clipboard()

    # ---- 視窗 ----

    def _window_at_cursor(self):
        tk = self.tk
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg="#d1d5db")
        frame = tk.Frame(win, bg=self.BG, padx=6, pady=6)
        frame.pack(padx=1, pady=1)
        return win, frame

    def _place(self, win) -> None:
        win.update_idletasks()
        x, y = cursor_pos()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        x, y = min(x + 12, sw - w - 8), y + 20
        if y + h > sh - 40:
            y = max(0, y - h - 30)
        win.geometry(f"+{x}+{y}")

    def show_popup(self) -> None:
        tk = self.tk
        win, frame = self._window_at_cursor()
        self.popup, self.sel, self.rows = win, 0, []
        for i, (text, _) in enumerate(self.options):
            row = tk.Label(frame, text=f"{i + 1}. {text}", font=self.FONT, anchor="w",
                           padx=10, pady=2, bg=self.BG, fg=self.FG, cursor="hand2")
            row.pack(fill="x")
            row.bind("<Button-1>", lambda e, i=i: self.choose(i))
            row.bind("<Enter>", lambda e, i=i: self.highlight(i))
            self.rows.append(row)
        hint = "1-9 選字 · Enter 確認 · Esc 取消" if len(self.options) > 1 else "Enter 套用 · Esc 取消"
        tk.Label(frame, text=hint, font=self.HINT_FONT, fg=self.HINT, bg=self.BG,
                 padx=10).pack(fill="x", pady=(4, 0))
        self.highlight(0)
        win.bind("<Key>", self.on_key)
        # 點到其他視窗時自動取消；但要先真的拿到過焦點，避免一出現就被關掉
        self.had_focus = False
        win.bind("<FocusIn>", lambda e: setattr(self, "had_focus", True))
        win.bind("<FocusOut>", lambda e: self.had_focus and
                 self.root.after(150, self._cancel_if_unfocused))
        self._place(win)
        win.deiconify()
        win.update()
        force_foreground(user32.GetAncestor(win.winfo_id(), GA_ROOT))
        win.focus_force()

    def _cancel_if_unfocused(self) -> None:
        if self.popup is not None and self.popup.focus_get() is None:
            self.cancel()

    def highlight(self, i: int) -> None:
        self.sel = i
        for j, row in enumerate(self.rows):
            on = j == i
            row.configure(bg=self.SEL_BG if on else self.BG, fg=self.SEL_FG if on else self.FG)

    def on_key(self, e) -> None:
        if e.keysym == "Escape":
            self.cancel()
        elif e.keysym in ("Return", "KP_Enter", "space"):
            self.choose(self.sel)
        elif e.keysym == "Down":
            self.highlight((self.sel + 1) % len(self.rows))
        elif e.keysym == "Up":
            self.highlight((self.sel - 1) % len(self.rows))
        elif e.char.isdigit() and 1 <= int(e.char) <= len(self.options):
            self.choose(int(e.char) - 1)

    def close_popup(self) -> None:
        if self.popup is not None:
            self.popup.destroy()
            self.popup = None

    def toast(self, message: str) -> None:
        tk = self.tk
        win, frame = self._window_at_cursor()
        tk.Label(frame, text=message, font=self.HINT_FONT, bg=self.BG, fg=self.FG,
                 padx=8, pady=2).pack()
        self._place(win)
        self.root.after(1500, win.destroy)
        self.restore_clipboard()

    def run(self) -> None:
        print(f"zhuyin-fix 已啟動：在任何程式選取亂碼後按 {HOTKEY_LABEL}。關閉這個視窗即結束。")
        self.root.mainloop()


def main() -> None:
    if sys.platform != "win32":
        sys.exit("zhuyin_hotkey.py 只支援 Windows；其他系統請用 zhuyin_fix.py。")
    _init_win32()
    App().run()


if __name__ == "__main__":
    main()
