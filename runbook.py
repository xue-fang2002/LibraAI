#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地服务启动控制台（科技树框架）
=================================
基于 book_manager_v3 的 runbook 控制面板改造，用于本项目（D:/xianmufile）的启停管理。

功能:
    - 自动检测本机局域网 IP
    - 自动读取 config/app.yaml 中的真实服务端口（与 app.py 保持一致）
    - 支持虚拟环境 / 全局 Python 自动切换（优先选择能 import flask 的解释器）
    - 实时状态监控（自动刷新）
    - 一键启动 / 停止 / 重启
    - 日志查看窗口
依赖:
    pip install psutil
    （tkinter 为标准库自带）
使用:
    直接双击运行，或命令行: python runbook.py
"""
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import subprocess
import os
import sys
import socket
import threading
import time
import json

try:
    import psutil
except ImportError:
    print("请先安装依赖: pip install psutil")
    sys.exit(1)

# ==================== 配置区 ====================
CONFIG_FILE = "runbook_config.json"
DEFAULT_CONFIG = {
    "python_path": "auto",  # "auto" 自动检测，或填绝对路径
    "script_name": "app.py",  # 后端主程序（本项目入口）
    "port": 5005,  # 服务端口（默认，启动时会以 config/app.yaml 为准自动对齐）
    "host": "0.0.0.0",  # 绑定地址
    "work_dir": "",  # 工作目录（空=脚本所在目录）
    "auto_refresh_interval": 1500,  # 状态刷新间隔（毫秒）
    "show_console": False,  # 是否显示控制台窗口
}
# ================================================

def load_app_port():
    """从项目 config/app.yaml 读取真实服务端口，确保监控端口与 app.py 一致"""
    try:
        import yaml
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "app.yaml")
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            app_sec = data.get("app") or {}
            return int(app_sec.get("port", 5005))
    except Exception:
        pass
    return 5005

def get_lan_ip():
    """获取本机局域网 IP"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def _python_can_import(py, mod):
    """判断某个 python 解释器能否 import 指定模块"""
    try:
        r = subprocess.run([py, "-c", f"import {mod}"],
                            capture_output=True, timeout=8)
        return r.returncode == 0
    except Exception:
        return False

def find_python():
    """查找可用的 Python 解释器（优先能 import flask 的，避免启动无依赖的解释器）"""
    # 1. 检查配置中的路径
    if config["python_path"] != "auto" and os.path.exists(config["python_path"]):
        return config["python_path"]
    # 2. 检查虚拟环境
    venv_candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv", "Scripts", "python.exe"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "venv", "Scripts", "python.exe"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "env", "Scripts", "python.exe"),
    ]
    for venv in venv_candidates:
        if os.path.exists(venv) and _python_can_import(venv, "flask"):
            return venv
    # 3. 检查全局 python / python3，优先能 import flask 的
    candidates = ["python.exe", "python3.exe", "python", "python3"]
    for cmd in candidates:
        if _python_can_import(cmd, "flask"):
            return cmd
    # 4. 兜底：任意能运行的 python
    for cmd in candidates:
        try:
            r = subprocess.run([cmd, "--version"], capture_output=True, timeout=5)
            if r.returncode == 0:
                return cmd
        except Exception:
            pass
    return None

def cmd_has_script(cmdline, script):
    """判断命令行是否以指定脚本名启动（按 basename 精确匹配，避免 app.py/xxx_app.py 误判）"""
    target = script.lower()
    for arg in cmdline:
        if os.path.basename(arg).lower() == target:
            return True
    return False

def get_server_pid(port=None):
    """
    查找指定端口的 app.py 进程
    如果 port 为 None，则查找所有 app.py 进程
    """
    target_script = config["script_name"]
    target_port = port or config["port"]
    for p in psutil.process_iter():
        try:
            cmdline = p.cmdline()
            if not cmdline:
                continue
            # 必须是以目标脚本名启动的进程
            if not cmd_has_script(cmdline, target_script):
                continue
            # 如果指定了端口，检查该进程占用的端口
            port_match = False
            if port is not None:
                try:
                    for conn in p.net_connections(kind="inet"):
                        if conn.laddr.port == target_port:
                            port_match = True
                            break
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    pass
            # 如果 port 为 None（查找所有），或者匹配到端口
            if port is None or port_match:
                return p.pid
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None

def get_all_instances():
    """获取所有运行的 app.py 实例"""
    instances = []
    target_script = config["script_name"]
    for p in psutil.process_iter():
        try:
            cmdline = p.cmdline()
            if not cmdline:
                continue
            if not cmd_has_script(cmdline, target_script):
                continue
            # 获取端口
            port = None
            try:
                for conn in p.net_connections(kind="inet"):
                    if conn.status == psutil.CONN_LISTEN:
                        port = conn.laddr.port
                        break
            except Exception:
                pass
            instances.append({
                "pid": p.pid,
                "port": port or "未知",
                "cmdline": cmdline,
                "create_time": time.strftime("%H:%M:%S", time.localtime(p.create_time()))
            })
        except Exception:
            pass
    return instances

def load_config():
    """加载配置"""
    global config
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                config = {**DEFAULT_CONFIG, **saved}
        except Exception:
            config = DEFAULT_CONFIG.copy()
    else:
        config = DEFAULT_CONFIG.copy()
    # 始终以 config/app.yaml 的端口为准，确保控制台监控端口与真实服务一致
    config["port"] = load_app_port()

def save_config():
    """保存配置"""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

def get_work_dir():
    """获取工作目录"""
    if config["work_dir"] and os.path.exists(config["work_dir"]):
        return config["work_dir"]
    return os.path.dirname(os.path.abspath(__file__))

# ==================== GUI 类 ====================
class ControlPanel:
    def __init__(self, root):
        self.root = root
        self.root.title("本地服务启动控制台")
        self.root.geometry("520x420")
        self.root.resizable(False, False)
        # 加载配置
        load_config()
        # 样式（蓝色主题，与界面风格统一）
        self.bg_color = "#f5f7fa"
        self.accent_color = "#2A4365"
        self.success_color = "#27ae60"
        self.danger_color = "#c0392b"
        self.warning_color = "#f39c12"
        self.root.configure(bg=self.bg_color)
        self._build_ui()
        self.refresh_status()
    def _build_ui(self):
        """构建界面"""
        # 标题栏
        title_frame = tk.Frame(self.root, bg=self.accent_color, height=50)
        title_frame.pack(fill=tk.X)
        title_frame.pack_propagate(False)
        tk.Label(
            title_frame, text="🚀 本地服务启动控制台",
            font=("微软雅黑", 14, "bold"), fg="white", bg=self.accent_color
        ).pack(side=tk.LEFT, padx=15, pady=10)
        # 状态卡片区域
        card_frame = tk.Frame(self.root, bg=self.bg_color)
        card_frame.pack(fill=tk.X, padx=15, pady=10)
        # 状态卡片
        self.status_card = self._create_card(card_frame, "运行状态", "检测中...", "#888")
        self.status_card.grid(row=0, column=0, padx=5, pady=5)
        self.port_card = self._create_card(card_frame, "服务端口", str(config["port"]), "#666")
        self.port_card.grid(row=0, column=1, padx=5, pady=5)
        self.ip_card = self._create_card(card_frame, "访问地址", get_lan_ip(), "#666")
        self.ip_card.grid(row=0, column=2, padx=5, pady=5)
        # 访问链接
        link_frame = tk.Frame(self.root, bg=self.bg_color)
        link_frame.pack(fill=tk.X, padx=15, pady=5)
        self.link_label = tk.Label(
            link_frame, text="",
            font=("微软雅黑", 11), fg=self.accent_color, bg=self.bg_color,
            cursor="hand2"
        )
        self.link_label.pack()
        self.link_label.bind("<Button-1>", self._open_browser)
        # 按钮区域
        btn_frame = tk.Frame(self.root, bg=self.bg_color)
        btn_frame.pack(pady=15)
        self.btn_start = tk.Button(
            btn_frame, text="▶ 启动", width=12, height=2,
            font=("微软雅黑", 11), bg=self.success_color, fg="white",
            activebackground="#219a52", cursor="hand2",
            command=self.start_server
        )
        self.btn_start.grid(row=0, column=0, padx=8)
        self.btn_stop = tk.Button(
            btn_frame, text="⏹ 停止", width=12, height=2,
            font=("微软雅黑", 11), bg=self.danger_color, fg="white",
            activebackground="#a93226", cursor="hand2",
            command=self.stop_server
        )
        self.btn_stop.grid(row=0, column=1, padx=8)
        self.btn_restart = tk.Button(
            btn_frame, text="🔄 重启", width=12, height=2,
            font=("微软雅黑", 11), bg=self.warning_color, fg="white",
            activebackground="#d68910", cursor="hand2",
            command=self.restart_server
        )
        self.btn_restart.grid(row=0, column=2, padx=8)
        # 信息区域
        info_frame = tk.Frame(self.root, bg=self.bg_color)
        info_frame.pack(fill=tk.X, padx=15, pady=5)
        # 实例列表
        self.instance_frame = tk.LabelFrame(
            self.root, text="运行中的实例", font=("微软雅黑", 10),
            bg=self.bg_color, fg="#333"
        )
        self.instance_frame.pack(fill=tk.X, padx=15, pady=5)
        self.instance_text = tk.Text(
            self.instance_frame, height=4, font=("Consolas", 10),
            bg="white", fg="#333", state=tk.DISABLED
        )
        self.instance_text.pack(fill=tk.X, padx=5, pady=5)
        # 底部按钮
        bottom_frame = tk.Frame(self.root, bg=self.bg_color)
        bottom_frame.pack(fill=tk.X, padx=15, pady=5)
        tk.Button(
            bottom_frame, text="⚙️ 配置", font=("微软雅黑", 9),
            bg="#e8e8e8", command=self.open_config
        ).pack(side=tk.LEFT)
        tk.Button(
            bottom_frame, text="📋 日志", font=("微软雅黑", 9),
            bg="#e8e8e8", command=self.show_log
        ).pack(side=tk.LEFT, padx=5)
        tk.Button(
            bottom_frame, text="🌐 访问网站", font=("微软雅黑", 9),
            bg=self.accent_color, fg="white",
            command=lambda: self._open_browser(None)
        ).pack(side=tk.RIGHT)
    def _create_card(self, parent, title, value, color):
        """创建信息卡片"""
        card = tk.Frame(parent, bg="white", width=150, height=70, bd=1, relief=tk.SOLID)
        card.grid_propagate(False)
        tk.Label(
            card, text=title, font=("微软雅黑", 9), fg="#888", bg="white"
        ).pack(pady=(8, 2))
        label = tk.Label(
            card, text=value, font=("微软雅黑", 12, "bold"),
            fg=color, bg="white"
        )
        label.pack()
        return card
    def _open_browser(self, event):
        """用浏览器打开网站"""
        ip = get_lan_ip()
        port = config["port"]
        url = f"http://{ip}:{port}"
        import webbrowser
        webbrowser.open(url)
    def start_server(self):
        """启动服务"""
        pid = get_server_pid()
        if pid:
            messagebox.showinfo("提示", f"端口 {config['port']} 的服务已在运行！\nPID: {pid}")
            return
        py_path = find_python()
        if not py_path:
            messagebox.showerror("错误", "未找到可用的 Python 解释器！\n请检查环境或手动配置路径。")
            return
        # 校验解释器能 import flask，否则给出明确提示
        if not _python_can_import(py_path, "flask"):
            messagebox.showerror(
                "依赖缺失",
                f"Python 解释器:\n{py_path}\n\n缺少 flask 依赖，无法启动服务。\n"
                f"请在该环境下执行: pip install -r requirements.txt"
            )
            return
        script_path = os.path.join(get_work_dir(), config["script_name"])
        if not os.path.exists(script_path):
            messagebox.showerror("错误", f"找不到后端程序: {script_path}")
            return
        try:
            cmd = [py_path, script_path]
            # 构建环境变量（app.py 以 config/app.yaml 为准，这里仅作为兼容预留）
            env = os.environ.copy()
            env["FLASK_PORT"] = str(config["port"])
            env["FLASK_HOST"] = config["host"]
            creation_flags = 0
            if os.name == "nt" and not config["show_console"]:
                creation_flags = subprocess.CREATE_NO_WINDOW
            subprocess.Popen(
                cmd,
                cwd=get_work_dir(),
                env=env,
                creationflags=creation_flags
            )
            # 等待启动
            time.sleep(2.5)
            self.refresh_status()
            pid = get_server_pid()
            if pid:
                ip = get_lan_ip()
                url = f"http://{ip}:{config['port']}"
                messagebox.showinfo("启动成功", f"服务已启动！\n\n访问地址: {url}\n进程 PID: {pid}")
            else:
                messagebox.showwarning("警告", "进程已启动，但可能尚未监听端口，请稍后刷新查看。")
        except Exception as e:
            messagebox.showerror("启动失败", f"错误: {str(e)}")
    def stop_server(self):
        """停止服务"""
        pid = get_server_pid()
        if not pid:
            messagebox.showinfo("提示", "当前没有运行的服务")
            return
        try:
            p = psutil.Process(pid)
            p.terminate()
            # 等待进程结束
            gone, alive = psutil.wait_procs([p], timeout=5)
            if alive:
                # 强制结束
                for proc in alive:
                    proc.kill()
            self.refresh_status()
            messagebox.showinfo("停止成功", f"服务已停止\nPID: {pid}")
        except Exception as e:
            messagebox.showerror("停止失败", f"错误: {str(e)}")
    def restart_server(self):
        """重启服务"""
        self.stop_server()
        time.sleep(1)
        self.start_server()
    def refresh_status(self):
        """刷新状态显示"""
        pid = get_server_pid()
        ip = get_lan_ip()
        port = config["port"]
        # 端口以 config/app.yaml 为准同步显示
        self.port_card.winfo_children()[1].config(text=str(port))
        if pid:
            self.status_card.winfo_children()[1].config(text="✅ 运行中", fg=self.success_color)
            self.link_label.config(text=f"http://{ip}:{port}")
            self.btn_start.config(state=tk.DISABLED)
            self.btn_stop.config(state=tk.NORMAL)
        else:
            self.status_card.winfo_children()[1].config(text="❌ 已停止", fg=self.danger_color)
            self.link_label.config(text="服务未运行")
            self.btn_start.config(state=tk.NORMAL)
            self.btn_stop.config(state=tk.DISABLED)
        # 刷新实例列表
        instances = get_all_instances()
        self.instance_text.config(state=tk.NORMAL)
        self.instance_text.delete(1.0, tk.END)
        if instances:
            for inst in instances:
                line = f"PID:{inst['pid']:6d}  端口:{str(inst['port']):>5s}  启动:{inst['create_time']}\n"
                self.instance_text.insert(tk.END, line)
        else:
            self.instance_text.insert(tk.END, "无运行中的实例\n")
        self.instance_text.config(state=tk.DISABLED)
        # 定时刷新
        self.root.after(config["auto_refresh_interval"], self.refresh_status)
    def open_config(self):
        """打开配置窗口"""
        cfg_win = tk.Toplevel(self.root)
        cfg_win.title("配置")
        cfg_win.geometry("400x350")
        cfg_win.resizable(False, False)
        cfg_win.transient(self.root)
        tk.Label(cfg_win, text="服务配置", font=("微软雅黑", 12, "bold")).pack(pady=10)
        # 端口
        frame1 = tk.Frame(cfg_win)
        frame1.pack(fill=tk.X, padx=20, pady=5)
        tk.Label(frame1, text="端口:", width=10, anchor=tk.E).pack(side=tk.LEFT)
        port_entry = tk.Entry(frame1, width=20)
        port_entry.insert(0, str(config["port"]))
        port_entry.pack(side=tk.LEFT)
        # Python 路径
        frame2 = tk.Frame(cfg_win)
        frame2.pack(fill=tk.X, padx=20, pady=5)
        tk.Label(frame2, text="Python路径:", width=10, anchor=tk.E).pack(side=tk.LEFT)
        py_entry = tk.Entry(frame2, width=25)
        py_entry.insert(0, config["python_path"])
        py_entry.pack(side=tk.LEFT)
        # 工作目录
        frame3 = tk.Frame(cfg_win)
        frame3.pack(fill=tk.X, padx=20, pady=5)
        tk.Label(frame3, text="工作目录:", width=10, anchor=tk.E).pack(side=tk.LEFT)
        dir_entry = tk.Entry(frame3, width=25)
        dir_entry.insert(0, config["work_dir"])
        dir_entry.pack(side=tk.LEFT)
        # 显示控制台
        frame4 = tk.Frame(cfg_win)
        frame4.pack(fill=tk.X, padx=20, pady=5)
        console_var = tk.BooleanVar(value=config["show_console"])
        tk.Checkbutton(frame4, text="显示控制台窗口（调试用）", variable=console_var).pack()

        def save():
            try:
                config["port"] = int(port_entry.get())
                config["python_path"] = py_entry.get().strip() or "auto"
                config["work_dir"] = dir_entry.get().strip()
                config["show_console"] = console_var.get()
                save_config()
                # 更新显示
                self.port_card.winfo_children()[1].config(text=str(config["port"]))
                messagebox.showinfo("保存成功", "配置已保存，重启控制面板后生效")
                cfg_win.destroy()
            except ValueError:
                messagebox.showerror("错误", "端口必须是数字")
        tk.Button(cfg_win, text="保存", width=10, command=save).pack(pady=20)
    def show_log(self):
        """显示日志窗口"""
        log_win = tk.Toplevel(self.root)
        log_win.title("运行日志")
        log_win.geometry("500x300")
        text = scrolledtext.ScrolledText(log_win, wrap=tk.WORD, font=("Consolas", 10))
        text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        # 显示进程信息
        instances = get_all_instances()
        text.insert(tk.END, f"当前时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        text.insert(tk.END, f"本机IP: {get_lan_ip()}\n")
        text.insert(tk.END, f"配置端口: {config['port']}（来自 config/app.yaml）\n")
        text.insert(tk.END, "-" * 50 + "\n")
        if instances:
            text.insert(tk.END, f"运行中的实例 ({len(instances)} 个):\n")
            for inst in instances:
                text.insert(tk.END, f"\nPID: {inst['pid']}\n")
                text.insert(tk.END, f"端口: {inst['port']}\n")
                text.insert(tk.END, f"启动时间: {inst['create_time']}\n")
                text.insert(tk.END, f"命令: {' '.join(inst['cmdline'][:5])}...\n")
        else:
            text.insert(tk.END, "没有运行中的实例\n")
        text.config(state=tk.DISABLED)

# ==================== 主程序 ====================
if __name__ == "__main__":
    root = tk.Tk()
    app = ControlPanel(root)
    root.mainloop()
