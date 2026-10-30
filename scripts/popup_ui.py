"""骑车提醒卡片：右上角淡入、置顶、可拖动，跟随系统深浅色。

由 ride_check.py 用 `popup_ui.py <json>` 在单独进程里调起；没有 tkinter 就退回系统弹窗。
"""
import json
import os
import platform
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ride_check  # noqa: E402

LIGHT = {
    "bg": "#FFFFFF", "border": "#D0D7DE", "fg": "#1F2328", "muted": "#656D76",
    "accent": "#1A7F37", "soft": "#DAFBE1", "chip": "#F6F8FA",
    "bad_bg": "#FFEBE9", "bad_fg": "#CF222E",
    "btn": "#1F883D", "btn_hover": "#1A7F37", "btn_fg": "#FFFFFF", "ghost_hover": "#F3F4F6",
}
DARK = {
    "bg": "#161B22", "border": "#30363D", "fg": "#E6EDF3", "muted": "#8D96A0",
    "accent": "#3FB950", "soft": "#1B3A26", "chip": "#21262D",
    "bad_bg": "#3D1D20", "bad_fg": "#FF7B72",
    "btn": "#238636", "btn_hover": "#2EA043", "btn_fg": "#FFFFFF", "ghost_hover": "#21262D",
}


def system_dark():
    system = platform.system()
    try:
        if system == "Windows":
            import winreg
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                               r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
        if system == "Darwin":
            import subprocess
            out = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"],
                                 capture_output=True, text=True).stdout
            return "Dark" in out
    except Exception:
        pass
    return False


def pick_family(tkfont):
    have = set(tkfont.families())
    for f in ("Microsoft YaHei UI", "PingFang SC", "Noto Sans CJK SC", "Noto Sans SC", "WenQuanYi Micro Hei"):
        if f in have:
            return f
    return "TkDefaultFont"


def rounded(cv, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


def show(card):
    import tkinter as tk
    import tkinter.font as tkfont

    win = platform.system() == "Windows"
    if win:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    c = DARK if system_dark() else LIGHT
    root = tk.Tk()
    root.withdraw()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    s = root.winfo_fpixels("1i") / 96

    def px(v):
        return int(round(v * s))

    fam = pick_family(tkfont)

    def F(size, weight="normal"):
        return (fam, size, weight)

    W = px(360)  # 内容宽
    outer = tk.Frame(root, bg=c["border"], padx=1, pady=1)
    outer.pack()
    body = tk.Frame(outer, bg=c["bg"])
    body.pack()
    tk.Frame(body, bg=c["accent"], height=px(4), width=W + px(40)).pack(fill="x")
    card_f = tk.Frame(body, bg=c["bg"], padx=px(20), pady=px(16))
    card_f.pack()

    def close(_=None):
        root.destroy()

    # 头部：图标 + 标题 + 关闭
    head = tk.Frame(card_f, bg=c["bg"])
    head.pack(fill="x")
    ic = px(40)
    icon = tk.Canvas(head, width=ic, height=ic, bg=c["bg"], highlightthickness=0)
    icon.pack(side="left")
    icon.create_oval(0, 0, ic, ic, fill=c["soft"], outline="")
    k = ic / 40
    lw = max(2, px(2))
    for cx in (12, 28):
        icon.create_oval((cx - 6) * k, 19 * k, (cx + 6) * k, 31 * k, outline=c["accent"], width=lw)
    for a, b in [((12, 25), (17, 15)), ((17, 15), (19, 25)), ((19, 25), (12, 25)),
                 ((19, 25), (26, 14)), ((26, 14), (28, 25)), ((15, 15), (20, 15)), ((24, 13), (28, 13))]:
        icon.create_line(a[0] * k, a[1] * k, b[0] * k, b[1] * k, fill=c["accent"], width=lw, capstyle="round")
    titles = tk.Frame(head, bg=c["bg"], padx=px(12))
    titles.pack(side="left")
    tk.Label(titles, text=card["title"], font=F(13, "bold"), bg=c["bg"], fg=c["fg"]).pack(anchor="w")
    tk.Label(titles, text=card["sub"], font=F(9), bg=c["bg"], fg=c["muted"]).pack(anchor="w")
    x = tk.Label(head, text="✕", font=F(11), bg=c["bg"], fg=c["muted"], cursor="hand2")
    x.pack(side="right", anchor="n")
    x.bind("<Button-1>", close)
    x.bind("<Enter>", lambda e: x.config(fg=c["fg"]))
    x.bind("<Leave>", lambda e: x.config(fg=c["muted"]))

    # 最佳时段
    big = tk.Frame(card_f, bg=c["bg"], pady=px(10))
    big.pack(fill="x")
    tk.Label(big, text=card["window"], font=F(24, "bold"), bg=c["bg"], fg=c["accent"]).pack(side="left")
    tk.Label(big, text="  最佳时段", font=F(9), bg=c["bg"], fg=c["muted"]).pack(side="left", anchor="s", pady=px(6))

    # 指标小块
    chips = tk.Frame(card_f, bg=c["bg"])
    chips.pack(fill="x")
    items = [("体感", card["feel"]), ("风", f"{card['wind']} km/h"), ("降水", f"{card['pp']}%")]
    if card.get("pm"):
        items.append(("PM2.5", card["pm"]))
    for i, (k_, v) in enumerate(items):
        ch = tk.Frame(chips, bg=c["chip"], padx=px(10), pady=px(6))
        ch.pack(side="left", padx=(0 if i == 0 else px(6), 0))
        tk.Label(ch, text=k_, font=F(8), bg=c["chip"], fg=c["muted"]).pack(anchor="w")
        tk.Label(ch, text=v, font=F(10, "bold"), bg=c["chip"], fg=c["fg"]).pack(anchor="w")

    # 逐小时条
    hours = card["hours"]
    lo, hi = card["best"]
    H = px(70)
    cv = tk.Canvas(card_f, width=W, height=H, bg=c["bg"], highlightthickness=0)
    cv.pack(pady=(px(14), 0))
    n = len(hours)
    cw = W / n
    gap = px(2)
    for i, h in enumerate(hours):
        x1, x2 = i * cw + gap, (i + 1) * cw - gap
        mid = (x1 + x2) / 2
        if h["feel"] is not None:
            cv.create_text(mid, px(8), text=f"{h['feel']:.0f}°", font=F(8), fill=c["muted"])
        if h["bad"]:
            fill, txt, tc = c["bad_bg"], h["bad"][0][0], c["bad_fg"]
        elif lo <= i < hi:
            fill, txt, tc = c["accent"], "", ""
        else:
            fill, txt, tc = c["soft"], "", ""
        rounded(cv, x1, px(18), x2, px(46), px(6), fill=fill, outline="")
        if txt:
            cv.create_text(mid, px(32), text=txt, font=F(8, "bold"), fill=tc)
        cv.create_text(mid, px(60), text=str(h["h"]), font=F(8), fill=c["muted"])

    tk.Label(card_f, text=card["tail"], font=F(9), bg=c["bg"], fg=c["fg"],
             wraplength=W, justify="left").pack(anchor="w", pady=(px(10), 0))
    tk.Label(card_f, text="骑完跟 Claude 说一声「今天骑车了」", font=F(8), bg=c["bg"],
             fg=c["muted"]).pack(anchor="w", pady=(px(2), 0))

    # 按钮
    btns = tk.Frame(card_f, bg=c["bg"], pady=0)
    btns.pack(fill="x", pady=(px(14), 0))

    def button(parent, text, primary, cmd):
        bg = c["btn"] if primary else c["bg"]
        hv = c["btn_hover"] if primary else c["ghost_hover"]
        b = tk.Label(parent, text=text, font=F(10, "bold" if primary else "normal"), bg=bg,
                     fg=c["btn_fg"] if primary else c["muted"], padx=px(16), pady=px(6), cursor="hand2")
        b.bind("<Button-1>", lambda e: cmd())
        b.bind("<Enter>", lambda e: b.config(bg=hv))
        b.bind("<Leave>", lambda e: b.config(bg=bg))
        return b

    def skip():
        try:
            ride_check.cmd_skip([card["day"]])
        finally:
            close()

    button(btns, f"好，{card['rel']}骑", True, close).pack(side="right")
    button(btns, "这周不骑了", False, skip).pack(side="right", padx=(0, px(6)))

    # 拖动、Esc 关闭
    drag = {}

    def start(e):
        drag.update(x=e.x_root - root.winfo_x(), y=e.y_root - root.winfo_y())

    def move(e):
        root.geometry(f"+{e.x_root - drag['x']}+{e.y_root - drag['y']}")

    for w in (card_f, head, titles, big, cv):
        w.bind("<ButtonPress-1>", start)
        w.bind("<B1-Motion>", move)
    root.bind("<Escape>", close)

    # 右上角（右下角常被任务栏和桌面挂件挡住）、圆角、淡入
    root.update_idletasks()
    w = root.winfo_reqwidth()
    root.geometry(f"+{root.winfo_screenwidth() - w - px(24)}+{px(24)}")
    if win:
        try:
            hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
            pref = ctypes.c_int(2)  # DWMWCP_ROUND，Windows 11 才有效
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
        except Exception:
            pass
        import winsound
        winsound.MessageBeep(0x40)
    root.attributes("-alpha", 0.0)
    root.deiconify()

    def fade(a=0.0):
        a = min(1.0, a + 0.08)
        root.attributes("-alpha", a)
        if a < 1.0:
            root.after(15, fade, a)

    fade()
    root.focus_force()
    root.mainloop()


if __name__ == "__main__":
    card = json.loads(sys.argv[1])
    try:
        show(card)
    except Exception as e:
        ride_check.log(f"卡片弹窗失败，退回系统弹窗：{e!r}")
        ride_check.plain_popup("🚲 " + card["title"], card["body"])
