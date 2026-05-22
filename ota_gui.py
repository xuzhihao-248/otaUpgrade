"""OTA 烧录工具 — GUI 模块。

ConfigWindow: 配置弹窗（设备连接、路径、版本、测试连接、保存）
UpgradeWindow: 主升级窗口（选择类型、固件、执行升级）
"""
import os
import sys
import threading
import tkinter as tk
from tkinter import scrolledtext, ttk, messagebox, filedialog

import ota_core

# 升级类型对应的固件角色和标签
UPGRADE_TYPE_ROLES = {
    "full": [("mcu", "MCU 固件"), ("sail", "Sail 固件"), ("switch", "Switch 固件"), ("ufs", "UFS 固件")],
    "mcu":  [("mcu", "MCU 固件")],
    "switch": [("switch", "Switch 固件")],
    "soc":  [("sail", "Sail 固件"), ("ufs", "UFS 固件")],
}


def _center_window(window, width, height):
    screen_w = window.winfo_screenwidth()
    screen_h = window.winfo_screenheight()
    x = (screen_w - width) // 2
    y = (screen_h - height) // 2
    window.geometry(f"{width}x{height}+{x}+{y}")


def _browse_file(entry, filetypes=None):
    path = filedialog.askopenfilename(filetypes=filetypes or [])
    if path:
        entry.delete(0, tk.END)
        entry.insert(0, path)


class ConfigWindow:
    """配置弹窗 — 直接在 root 中构建，确认后切换到 UpgradeWindow。"""

    def __init__(self, root, on_confirm):
        self.root = root
        self.on_confirm = on_confirm
        self.root.title("配置 - OTA 升级工具")
        self.root.resizable(True, True)
        _center_window(self.root, 520, 520)

        try:
            self.defaults = ota_core.load_config()
        except Exception as e:
            messagebox.showerror("错误", f"无法加载 config.json:\n{e}")
            root.destroy()
            return

        self._build()

    def _build(self):
        self.main_frame = ttk.Frame(self.root, padding="10")
        self.main_frame.pack(fill=tk.BOTH, expand=True)

        frame = self.main_frame
        row = 0

        # — 设备连接 —
        ttk.Label(frame, text="设备连接", font=("微软雅黑", 10, "bold")).grid(row=row, column=0, columnspan=2,
                                                                              sticky=tk.W, pady=(0, 2))
        row += 1
        dev = self.defaults.get('device', {})
        self.host_var = tk.StringVar(value=dev.get('host', ''))
        self.user_var = tk.StringVar(value=dev.get('user', ''))
        self.pw_var = tk.StringVar(value=dev.get('pw', ''))
        for label, var in [("主机 IP:", self.host_var), ("用户名:", self.user_var), ("密码:", self.pw_var)]:
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky=tk.W, pady=2)
            show = '' if '密码' in label else None
            entry = ttk.Entry(frame, textvariable=var, width=42, show=show)
            entry.grid(row=row, column=1, sticky=tk.EW, pady=2)
            row += 1

        # — 路径配置 —
        ttk.Label(frame, text="路径配置", font=("微软雅黑", 10, "bold")).grid(row=row, column=0, columnspan=2,
                                                                              sticky=tk.W, pady=(8, 2))
        row += 1
        paths = self.defaults.get('paths', {})
        self.bin_var = tk.StringVar(value=paths.get('bin_path', ''))
        self.lib_var = tk.StringVar(value=paths.get('lib_paths', ''))
        self.remote_var = tk.StringVar(value=paths.get('remote_ota_dir', ''))
        self.local_var = tk.StringVar(value=paths.get('local_image_dir', ''))

        path_fields = [
            ("bin_path:", self.bin_var, None),
            ("lib_paths:", self.lib_var, None),
            ("remote_ota_dir:", self.remote_var, None),
            ("local_image_dir:", self.local_var, "dir"),
        ]
        for label, var, browse_type in path_fields:
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky=tk.W, pady=2)
            f = ttk.Frame(frame)
            f.grid(row=row, column=1, sticky=tk.EW, pady=2)
            entry = ttk.Entry(f, textvariable=var, width=32)
            entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
            if browse_type == "dir":
                ttk.Button(f, text="浏览", command=lambda v=var: self._browse_dir(v)).pack(side=tk.LEFT, padx=4)
            row += 1

        # — 版本校验 —
        ttk.Label(frame, text="预期版本", font=("微软雅黑", 10, "bold")).grid(row=row, column=0, columnspan=2,
                                                                              sticky=tk.W, pady=(8, 2))
        row += 1
        ev = self.defaults.get('expected_versions', {})
        self.mcu_ver_var = tk.StringVar(value=ev.get('mcu', ''))
        self.ufs_ver_var = tk.StringVar(value=ev.get('ufs', ''))
        self.switch_ver_var = tk.StringVar(value=ev.get('switch', ''))
        for label, var in [("MCU 版本:", self.mcu_ver_var), ("UFS 版本:", self.ufs_ver_var),
                            ("Switch 版本:", self.switch_ver_var)]:
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky=tk.W, pady=2)
            ttk.Entry(frame, textvariable=var, width=42).grid(row=row, column=1, sticky=tk.EW, pady=2)
            row += 1

        frame.columnconfigure(1, weight=1)

        # — 按钮 —
        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=row, column=0, columnspan=2, pady=(12, 0))
        ttk.Button(btn_frame, text="测试连接", command=self._test_connection).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="保存为默认配置", command=self._save).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="确定", command=self._confirm).pack(side=tk.LEFT, padx=4)

    def _browse_dir(self, var):
        path = filedialog.askdirectory()
        if path:
            var.set(path)

    def _collect_config(self):
        return {
            "device": {"host": self.host_var.get(), "user": self.user_var.get(), "pw": self.pw_var.get()},
            "paths": {"bin_path": self.bin_var.get(), "lib_paths": self.lib_var.get(),
                      "remote_ota_dir": self.remote_var.get(), "local_image_dir": self.local_var.get()},
            "expected_versions": {"mcu": self.mcu_ver_var.get(), "ufs": self.ufs_ver_var.get(),
                                  "switch": self.switch_ver_var.get()},
        }

    def _test_connection(self):
        host = self.host_var.get()
        user = self.user_var.get()
        pw = self.pw_var.get()
        if not host:
            messagebox.showwarning("提示", "请先填写主机 IP")
            return
        ok, err = ota_core.ssh_test_connection(host, user, pw)
        if ok:
            messagebox.showinfo("成功", "SSH 连接成功！")
        else:
            messagebox.showerror("失败", f"SSH 连接失败:\n{err}")

    def _save(self):
        config = self._collect_config()
        config.setdefault('firmware_files', self.defaults.get('firmware_files', {}))
        config.setdefault('zip_extracted_files', self.defaults.get('zip_extracted_files', {}))
        try:
            ota_core.save_config(config)
            self.defaults = config  # 更新内存中的默认值
            messagebox.showinfo("已保存", "配置已保存为默认值")
        except Exception as e:
            messagebox.showerror("保存失败", str(e))

    def _confirm(self):
        self.main_frame.destroy()
        self.on_confirm(self._collect_config(), self.defaults)


class FileRow:
    """固件文件选择行：标签 + 输入框 + 浏览按钮。"""

    def __init__(self, parent, label, default_path, local_dir):
        self.label = label
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X, pady=2)
        ttk.Label(frame, text=label, width=12).pack(side=tk.LEFT)
        self.var = tk.StringVar(value=os.path.join(local_dir, default_path) if default_path else '')
        self.entry = ttk.Entry(frame, textvariable=self.var)
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        ttk.Button(frame, text="浏览", command=self._browse).pack(side=tk.LEFT)

    def _browse(self):
        path = filedialog.askopenfilename()
        if path:
            self.var.set(path)

    def get(self):
        return self.var.get()


class UpgradeWindow:
    """主升级窗口。"""

    def __init__(self, root, config, defaults):
        self.root = root
        self.root.title("OTA 升级工具")
        self.root.geometry("900x700")
        self.root.resizable(True, True)
        self.config = config
        self.defaults = defaults
        self.running = False
        self.file_rows = []
        self.extracted_entries = {}

        style = ttk.Style()
        style.configure("TButton", font=("微软雅黑", 10), padding=6)
        style.configure("TLabel", font=("微软雅黑", 10))

        self._build()

    def _build(self):
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # — 升级类型 —
        type_frame = ttk.LabelFrame(main_frame, text="升级类型", padding="5")
        type_frame.pack(fill=tk.X, pady=(0, 8))
        self.upgrade_type_var = tk.StringVar(value="full")
        for val, text in [("full", "一键完整升级"), ("mcu", "单独升级 MCU"),
                           ("switch", "单独升级 Switch"), ("soc", "单独升级 SoC")]:
            ttk.Radiobutton(type_frame, text=text, variable=self.upgrade_type_var, value=val,
                            command=self._on_type_change).pack(side=tk.LEFT, padx=10)

        # — 固件来源 —
        src_frame = ttk.LabelFrame(main_frame, text="固件来源", padding="5")
        src_frame.pack(fill=tk.X, pady=(0, 8))
        self.source_var = tk.StringVar(value="direct")
        ttk.Radiobutton(src_frame, text="单个文件", variable=self.source_var, value="direct",
                        command=self._on_source_change).pack(side=tk.LEFT, padx=10)
        ttk.Radiobutton(src_frame, text="ZIP 压缩包", variable=self.source_var, value="zip",
                        command=self._on_source_change).pack(side=tk.LEFT, padx=10)

        # — 文件选择区（动态）—
        self.file_area = ttk.LabelFrame(main_frame, text="固件文件", padding="5")
        self.file_area.pack(fill=tk.X, pady=(0, 8))
        self._rebuild_file_area()

        # — 操作按钮 —
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(0, 8))
        self.btn_start = ttk.Button(btn_frame, text="开始升级", command=self._start_upgrade)
        self.btn_start.pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="清空日志", command=self._clear_log).pack(side=tk.RIGHT, padx=5)

        # — 日志 —
        log_frame = ttk.LabelFrame(main_frame, text="升级日志", padding="5")
        log_frame.pack(fill=tk.BOTH, expand=True)
        self.log_text = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # — 状态栏 —
        status_frame = ttk.Frame(main_frame)
        status_frame.pack(fill=tk.X, pady=(5, 0))
        self.status_label = ttk.Label(status_frame, text="就绪", relief=tk.SUNKEN, anchor=tk.W,
                                      font=("微软雅黑", 9))
        self.status_label.pack(fill=tk.X)

        # — 进度条 —
        self.progress = ttk.Progressbar(main_frame, mode='indeterminate')
        self.progress.pack(fill=tk.X, pady=(5, 0))

    def _rebuild_file_area(self):
        for w in self.file_area.winfo_children():
            w.destroy()
        self.file_rows = []
        self.extracted_entries = {}

        is_zip = self.source_var.get() == 'zip'
        roles = UPGRADE_TYPE_ROLES.get(self.upgrade_type_var.get(), [])
        local_dir = self.config.get('paths', {}).get('local_image_dir', './images')
        defaults_fw = self.defaults.get('firmware_files', {})
        upgrade_type = self.upgrade_type_var.get()
        defaults_for_type = defaults_fw.get(upgrade_type, {})

        if is_zip:
            # zip 模式：一个 zip 文件选择 + extracted_files 编辑器
            zip_frame = ttk.Frame(self.file_area)
            zip_frame.pack(fill=tk.X, pady=2)
            ttk.Label(zip_frame, text="ZIP 文件:", width=12).pack(side=tk.LEFT)
            self.zip_var = tk.StringVar()
            self.zip_entry = ttk.Entry(zip_frame, textvariable=self.zip_var)
            self.zip_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
            ttk.Button(zip_frame, text="浏览", command=lambda: _browse_file(
                self.zip_entry, [("ZIP 压缩包", "*.zip")])).pack(side=tk.LEFT)

            ttk.Label(self.file_area, text="解压后文件名（可修改）:", font=("微软雅黑", 9)).pack(anchor=tk.W, pady=(8, 2))
            defaults_extracted = self.defaults.get('zip_extracted_files', {})
            for role, label_text in roles:
                f = ttk.Frame(self.file_area)
                f.pack(fill=tk.X, pady=2)
                ttk.Label(f, text=label_text, width=12).pack(side=tk.LEFT)
                default_name = defaults_extracted.get(role, defaults_for_type.get(role, ''))
                var = tk.StringVar(value=default_name)
                ttk.Entry(f, textvariable=var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
                self.extracted_entries[role] = var
        else:
            # direct 模式：每个角色一个文件选择行
            for role, label_text in roles:
                default_name = defaults_for_type.get(role, '')
                row = FileRow(self.file_area, label_text, default_name, local_dir)
                self.file_rows.append((role, row))

    def _on_type_change(self):
        self._rebuild_file_area()

    def _on_source_change(self):
        self._rebuild_file_area()

    def _clear_log(self):
        self.log_text.delete(1.0, tk.END)

    # — 回调（UI 线程安全）—
    def _safe_log(self, message, end='\n'):
        def _do():
            self.log_text.insert(tk.END, message + end)
            self.log_text.see(tk.END)
            self.root.update_idletasks()
        self.root.after(0, _do)

    def _safe_status(self, text):
        self.root.after(0, lambda: self.status_label.config(text=text))

    def _safe_progress(self, filename, transferred, total):
        percent = (transferred / total) * 100 if total > 0 else 0
        self.root.after(0, lambda: self.status_label.config(
            text=f"上传 {filename}: {percent:.1f}% ({transferred}/{total} bytes)"))

    def _set_buttons_state(self, enabled):
        state = tk.NORMAL if enabled else tk.DISABLED
        self.root.after(0, lambda: self.btn_start.config(state=state))

    def _start_progress(self):
        self.progress.start(10)
        self._set_buttons_state(False)

    def _stop_progress(self):
        self.progress.stop()
        self._set_buttons_state(True)

    # — 升级触发 —
    def _start_upgrade(self):
        if self.running:
            messagebox.showwarning("提示", "已有升级任务正在运行")
            return

        upgrade_type = self.upgrade_type_var.get()
        is_zip = self.source_var.get() == 'zip'
        roles = UPGRADE_TYPE_ROLES.get(upgrade_type, [])

        run_config = {
            "device": self.config.get("device", {}),
            "paths": self.config.get("paths", {}),
            "expected_versions": self.config.get("expected_versions", {}),
            "mode": "zip" if is_zip else "direct",
            "firmware_files": {},
            "extracted_files": {},
        }

        if is_zip:
            zip_path = self.zip_var.get()
            if not zip_path:
                messagebox.showwarning("提示", "请选择 ZIP 压缩包")
                return
            run_config["zip_file"] = zip_path
            for role, _ in roles:
                run_config["extracted_files"][role] = self.extracted_entries.get(role, tk.StringVar()).get()
        else:
            for role, row in self.file_rows:
                path = row.get()
                if not path:
                    messagebox.showwarning("提示", f"请选择 {row.label} 的文件")
                    return
                run_config["firmware_files"][role] = os.path.basename(path)
                # 将完整目录路径也传给 config 供上传使用
                dirname = os.path.dirname(path)
                if dirname:
                    run_config["paths"]["local_image_dir"] = dirname

        callbacks = {
            "log": self._safe_log,
            "status": self._safe_status,
            "progress": self._safe_progress,
        }

        flow_map = {
            "full": ota_core.run_full_upgrade,
            "mcu": ota_core.run_mcu_upgrade,
            "switch": ota_core.run_switch_upgrade,
            "soc": ota_core.run_soc_upgrade,
        }
        flow_func = flow_map.get(upgrade_type)
        if not flow_func:
            messagebox.showerror("错误", f"未知升级类型: {upgrade_type}")
            return

        self.running = True
        self._start_progress()
        self._safe_log("=" * 60)
        self._safe_log("开始新的升级任务")

        def worker():
            try:
                flow_func(run_config, callbacks)
            except Exception as e:
                self._safe_log(f"\n[致命错误] {e}")
                messagebox.showerror("升级错误", str(e))
            finally:
                self.running = False
                self._stop_progress()
                self._safe_log("\n升级任务结束。")
                self._safe_status("就绪")

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()


def run():
    root = tk.Tk()

    def on_confirm(config, defaults):
        UpgradeWindow(root, config, defaults)

    ConfigWindow(root, on_confirm)
    root.mainloop()
