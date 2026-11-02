# -*- coding: utf-8 -*-
"""Talos Studio 安装向导

一个正经的 PC 安装流程：欢迎 → 选目录 → 进度 → 完成。
内置 Python 运行时，离线安装，装完直接能用。
"""
import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

APP_NAME = "Talos Studio"
APP_ID = "TalosStudio"
DEFAULT_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / APP_ID

JADE = "#0E7A4E"
JADE2 = "#19A26A"
INK = "#1C1A15"
DIM = "#6E7A73"
BG = "#FBFAF7"
LINE = "#E3DFD2"


def base_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent))


class Installer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} 安装程序")
        self.geometry("620x430")
        self.resizable(False, False)
        self.configure(bg=BG)
        try:
            self.iconbitmap(str(base_dir() / "app.ico"))
        except Exception:
            pass
        self.target = tk.StringVar(value=str(DEFAULT_DIR))
        self.launch = tk.BooleanVar(value=True)
        self.step = 0
        self._build_head()
        self.body = tk.Frame(self, bg=BG)
        self.body.pack(fill="both", expand=True, padx=34, pady=(0, 0))
        self._page_welcome()

    # ---------- 共用头部 ----------
    def _build_head(self):
        head = tk.Frame(self, bg=BG, height=104)
        head.pack(fill="x", padx=34, pady=(26, 0))
        try:
            img = tk.PhotoImage(file=str(base_dir() / "logo_96.png"))
            tk.Label(head, image=img, bg=BG).pack(side="left", padx=(0, 16))
            self._img = img
        except Exception:
            pass
        box = tk.Frame(head, bg=BG)
        box.pack(side="left", anchor="w")
        tk.Label(box, text=APP_NAME, bg=BG, fg=INK,
                 font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w")
        tk.Label(box, text="让每个 AI 形象都能开播", bg=BG, fg=DIM,
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(3, 0))
        tk.Frame(self, bg=LINE, height=1).pack(fill="x", padx=34, pady=(18, 0))

    def _clear(self):
        for w in self.body.winfo_children():
            w.destroy()

    # ---------- 页面 1 ----------
    def _page_welcome(self):
        self._clear()
        f = self.body
        tk.Label(f, text="安装到", bg=BG, fg=DIM,
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(18, 6))
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x")
        entry = tk.Entry(row, textvariable=self.target, font=("Consolas", 9),
                         bg="#FFFFFF", fg=INK, relief="solid", bd=1)
        entry.pack(side="left", fill="x", expand=True, ipady=5)
        tk.Button(row, text="浏览…", command=self._browse, bg="#FFFFFF", fg=INK,
                  relief="solid", bd=1, padx=12, pady=3,
                  font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(8, 0))

        info = tk.Frame(f, bg="#F3FAF6", highlightbackground="#D8EAE0", highlightthickness=1)
        info.pack(fill="x", pady=(16, 0), ipady=10)
        tk.Label(info, text="· 自带运行环境，不需要装 Python，也不需要联网\n"
                            "· 大约占用 300 MB 磁盘空间\n"
                            "· 会创建桌面和开始菜单快捷方式",
                 bg="#F3FAF6", fg="#3F5B4C", justify="left",
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=14)

        tk.Checkbutton(f, text="装完后立即启动", variable=self.launch, bg=BG, fg=INK,
                       activebackground=BG, selectcolor="#FFFFFF",
                       font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(16, 0))

        btns = tk.Frame(f, bg=BG)
        btns.pack(side="bottom", fill="x", pady=(0, 20))
        tk.Button(btns, text="退出", command=self.destroy, bg="#FFFFFF", fg=DIM,
                  relief="solid", bd=1, padx=20, pady=7,
                  font=("Microsoft YaHei UI", 9)).pack(side="right")
        tk.Button(btns, text="立即安装", command=self._page_progress, bg=JADE, fg="#FFFFFF",
                  activebackground=JADE2, activeforeground="#FFFFFF", relief="flat",
                  padx=26, pady=7, font=("Microsoft YaHei UI", 10, "bold")).pack(side="right", padx=(0, 10))

    def _browse(self):
        d = filedialog.askdirectory(title="选择安装位置", initialdir=str(Path(self.target.value).parent))
        if d:
            self.target.set(str(Path(d) / APP_ID))

    # ---------- 页面 2 ----------
    def _page_progress(self):
        self._clear()
        f = self.body
        tk.Label(f, text="正在安装…", bg=BG, fg=INK,
                 font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", pady=(24, 10))
        self.status = tk.Label(f, text="准备中", bg=BG, fg=DIM, font=("Microsoft YaHei UI", 9))
        self.status.pack(anchor="w")
        self.bar = ttk.Progressbar(f, length=550, mode="determinate")
        self.bar.pack(fill="x", pady=(10, 0))
        ttk.Style().configure("TProgressbar", background=JADE)
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        try:
            target = Path(self.target.value)
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            target.mkdir(parents=True, exist_ok=True)

            payload = base_dir() / "payload.zip"
            with zipfile.ZipFile(payload) as z:
                items = z.infolist()
                total = len(items)
                for i, item in enumerate(items):
                    z.extract(item, target)
                    if i % 60 == 0:
                        pct = int(i / total * 100)
                        self.after(0, lambda p=pct: (self.bar.configure(value=p),
                                                     self.status.configure(text=f"解压文件 {p}%")))
            self.after(0, lambda: self.bar.configure(value=100))
            self._shortcuts(target)
            self._uninstall_entry(target)
            self.after(0, lambda: self._page_done(target))
        except Exception as e:
            self.after(0, lambda: (messagebox.showerror("安装失败", str(e)), self.destroy()))

    def _shortcuts(self, target: Path):
        exe = target / "启动.bat"
        ico = target / "static" / "logo.ico"
        ps = (
            "$w=New-Object -ComObject WScript.Shell;"
            f"$d=$w.CreateShortcut([Environment]::GetFolderPath('Desktop')+'\\{APP_NAME}.lnk');"
            f"$d.TargetPath='{exe}';$d.WorkingDirectory='{target}';$d.IconLocation='{ico}';$d.Save();"
            f"$s=$w.CreateShortcut([Environment]::GetFolderPath('Programs')+'\\{APP_NAME}.lnk');"
            f"$s.TargetPath='{exe}';$s.WorkingDirectory='{target}';$s.IconLocation='{ico}';$s.Save()"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)

    def _uninstall_entry(self, target: Path):
        """写一条卸载记录，让它出现在 Windows 的“应用和功能”里。"""
        uninst = target / "卸载.bat"
        uninst.write_text(
            "@echo off\r\n"
            "chcp 65001 >nul\r\n"
            "echo 正在卸载 Talos Studio...\r\n"
            "taskkill /IM python.exe /F >nul 2>nul\r\n"
            f"del \"%USERPROFILE%\\Desktop\\{APP_NAME}.lnk\" >nul 2>nul\r\n"
            f"del \"%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\{APP_NAME}.lnk\" >nul 2>nul\r\n"
            "reg delete \"HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\TalosStudio\" /f >nul 2>nul\r\n"
            f"timeout /t 2 >nul\r\n"
            f"rmdir /s /q \"{target}\"\r\n"
            "echo 卸载完成。\r\n"
            "pause\r\n", encoding="gbk", errors="replace")
        ps = (
            "$k='HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\TalosStudio';"
            "New-Item -Path $k -Force | Out-Null;"
            f"Set-ItemProperty $k DisplayName '{APP_NAME}';"
            f"Set-ItemProperty $k DisplayIcon '{target}\\static\\logo.ico';"
            f"Set-ItemProperty $k InstallLocation '{target}';"
            f"Set-ItemProperty $k UninstallString 'cmd /c \"\"{target}\\卸载.bat\"\"';"
            "Set-ItemProperty $k DisplayVersion '1.0.0';"
            "Set-ItemProperty $k Publisher 'Talos';"
            "Set-ItemProperty $k NoModify 1 -Type DWord;"
            "Set-ItemProperty $k NoRepair 1 -Type DWord"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)

    # ---------- 页面 3 ----------
    def _page_done(self, target: Path):
        self._clear()
        f = self.body
        tk.Label(f, text="安装完成", bg=BG, fg=JADE,
                 font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w", pady=(22, 6))
        tk.Label(f, text=f"已安装到 {target}", bg=BG, fg=DIM,
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w")

        tip = tk.Frame(f, bg="#FBF3E4", highlightbackground="#EEDCB8", highlightthickness=1)
        tip.pack(fill="x", pady=(18, 0), ipady=10)
        tk.Label(tip, text="开播还需要两个官方软件（不装也能先做形象、试聊天）：",
                 bg="#FBF3E4", fg="#8A6520", font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=14)
        rowb = tk.Frame(tip, bg="#FBF3E4")
        rowb.pack(anchor="w", padx=14, pady=(8, 0))
        tk.Button(rowb, text="下载 OBS", relief="solid", bd=1, bg="#FFFFFF", fg=INK, padx=12, pady=3,
                  font=("Microsoft YaHei UI", 9),
                  command=lambda: self._open("https://obsproject.com/download")).pack(side="left")
        tk.Button(rowb, text="下载抖音直播伴侣", relief="solid", bd=1, bg="#FFFFFF", fg=INK, padx=12, pady=3,
                  font=("Microsoft YaHei UI", 9),
                  command=lambda: self._open("https://streamingtool.douyin.com/")).pack(side="left", padx=(8, 0))

        btns = tk.Frame(f, bg=BG)
        btns.pack(side="bottom", fill="x", pady=(0, 20))
        tk.Button(btns, text="完成", command=self.destroy, bg="#FFFFFF", fg=DIM, relief="solid", bd=1,
                  padx=20, pady=7, font=("Microsoft YaHei UI", 9)).pack(side="right")
        tk.Button(btns, text="启动 Studio", command=lambda: self._launch(target), bg=JADE, fg="#FFFFFF",
                  activebackground=JADE2, activeforeground="#FFFFFF", relief="flat", padx=22, pady=7,
                  font=("Microsoft YaHei UI", 10, "bold")).pack(side="right", padx=(0, 10))
        if self.launch.get():
            self.after(400, lambda: self._launch(target))

    def _launch(self, target: Path):
        subprocess.Popen([str(target / "启动.bat")], cwd=str(target), shell=True)
        self.after(1500, self.destroy)

    @staticmethod
    def _open(url: str):
        import webbrowser
        webbrowser.open(url)


if __name__ == "__main__":
    try:
        Installer().mainloop()
    except Exception as exc:  # 兜底，别让安装器静默死掉
        import traceback
        Path(os.path.expanduser("~"), "talos-install-error.log").write_text(
            traceback.format_exc(), encoding="utf-8")
        raise exc
