#!/usr/bin/env python3
"""
OTA 升级工具 - GUI 版本
支持一键升级（原有完整流程）以及单独升级 MCU、Switch、SoC。
优化了进度显示：上传文件和命令中的百分比进度通过状态栏单行显示。
MCU 升级时不在日志区域打印命令实时输出，仅显示关键提示和进度条。
新增“清空日志”按钮，可随时清空日志区域。
"""

import paramiko
import os
import sys
import json
import time
import re
import socket
import threading
import tkinter as tk
from tkinter import scrolledtext, ttk, messagebox
from typing import Dict, List, Tuple, Any, Callable

# ========================= 辅助函数 =========================

def load_config() -> Dict[str, Any]:
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"找不到配置文件: {config_path}")
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def progress_callback(filename: str, transferred: int, total: int, status_update_func: Callable):
    percent = (transferred / total) * 100
    status_update_func(f"上传 {filename}: {percent:.1f}% ({transferred}/{total} bytes)")


def execute_normal_command(ssh: paramiko.SSHClient, cmd: str, env_fix: str,
                           log_func: Callable, status_update_func: Callable,
                           timeout: int = 300, verbose: bool = True) -> Tuple[int, str]:
    """
    执行普通命令，持续读取输出避免管道阻塞，返回 (退出码, 输出内容)
    verbose: 是否将命令实时输出打印到日志区域（True：打印；False：仅更新状态栏）
    """
    full_cmd = f"{env_fix} {cmd}"
    log_func(f"\n[执行]: {cmd}")
    stdin, stdout, stderr = ssh.exec_command(full_cmd)

    out_chunks = []
    err_chunks = []
    start_time = time.time()

    while not stdout.channel.exit_status_ready():
        if time.time() - start_time > timeout:
            raise TimeoutError(f"命令 '{cmd}' 执行超过 {timeout} 秒仍未结束")
        if stdout.channel.recv_ready():
            data = stdout.channel.recv(4096).decode('utf-8', errors='ignore')
            out_chunks.append(data)
            if verbose:
                log_func(data, end='')
            # 始终更新状态栏（显示进度百分比）
            if '%' in data:
                for line in data.splitlines():
                    if '%' in line:
                        status_update_func(line.strip()[:80])
        if stderr.channel.recv_stderr_ready():
            data = stderr.channel.recv_stderr(4096).decode('utf-8', errors='ignore')
            err_chunks.append(data)
            if data.strip() and verbose:
                log_func(f"[stderr] {data}", end='')
        time.sleep(0.05)

    exit_code = stdout.channel.recv_exit_status()
    remaining_out = stdout.read().decode('utf-8', errors='ignore')
    remaining_err = stderr.read().decode('utf-8', errors='ignore')
    out_msg = ''.join(out_chunks) + remaining_out
    err_msg = ''.join(err_chunks) + remaining_err
    out_msg = out_msg.strip()
    err_msg = err_msg.strip()

    if exit_code != 0:
        log_func(f"[❌ 失败] 退出码: {exit_code}")
        if err_msg:
            log_func(f"[错误详情]: {err_msg}")
    else:
        log_func("[✔ 成功]")
    return exit_code, out_msg


def execute_reboot_command(ssh: paramiko.SSHClient, cmd: str, env_fix: str, log_func: Callable) -> Tuple[int, str]:
    full_cmd = f"{env_fix} {cmd}"
    log_func(f"\n[执行]: {cmd}")
    try:
        stdin, stdout, stderr = ssh.exec_command(full_cmd)
        time.sleep(0.5)
        out_chunks = []
        if stdout.channel.recv_ready():
            out_chunks.append(stdout.channel.recv(4096).decode('utf-8', errors='ignore'))
        out_msg = ''.join(out_chunks)
        if out_msg:
            log_func(out_msg)
        log_func("[✔ 重启命令已发送，设备将重启]")
    except (socket.error, paramiko.SSHException, EOFError) as e:
        log_func(f"[!] 连接已断开（预期行为），重启命令成功。")
        return 0, ""
    except Exception as e:
        log_func(f"[❌] 执行重启命令时出现未知异常: {e}")
        return -1, ""
    return 0, ""


def upload_files(ssh: paramiko.SSHClient, remote_dir: str, local_dir: str,
                 files: List[str], log_func: Callable, status_update_func: Callable) -> None:
    sftp = ssh.open_sftp()
    try:
        sftp.mkdir(remote_dir)
    except IOError:
        pass
    log_func(f"[*] 正在同步本地文件至 {remote_dir} ...")
    for f in files:
        local_path = os.path.join(local_dir, f)
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"找不到文件: {local_path}")
        remote_path = f"{remote_dir}/{f}"
        sftp.put(local_path, remote_path,
                 callback=lambda t, total, name=f: progress_callback(name, t, total, status_update_func))
    sftp.close()
    log_func("[✔] 所有文件上传完成")
    status_update_func("就绪")


def wait_reboot_and_reconnect(host: str, user: str, pw: str, log_func: Callable,
                              wait_seconds: int = 30, retries: int = 5) -> paramiko.SSHClient:
    log_func(f"\n[*] 已完成复位指令，进入 {wait_seconds}s 硬件重启等待期...")
    time.sleep(wait_seconds)
    log_func(f"[*] 正在尝试重新连接设备 {host}...")
    for attempt in range(1, retries + 1):
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(host, username=user, password=pw, timeout=15)
            log_func("[✔] 重连成功！")
            return ssh
        except Exception as e:
            log_func(f"[!] 等待设备上线中 ({attempt}/{retries})... {e}")
            time.sleep(10)
    raise RuntimeError("无法重新连接到设备")


def parse_versions(ssh: paramiko.SSHClient, env_fix: str,
                   log_func: Callable, status_update_func: Callable) -> Dict[str, str]:
    _, mcu_res = execute_normal_command(ssh, "ota_tool show-version", env_fix, log_func, status_update_func)
    mcu_match = re.search(r'MCU Version:\s*(\S+)', mcu_res)
    mcu = mcu_match.group(1) if mcu_match else "N/A"

    _, sw_res = execute_normal_command(ssh, "switch_bcm_flasher -v", env_fix, log_func, status_update_func)
    sw_match = re.search(r'software version\s*:\s*GTMC_(\S+)', sw_res)
    switch = sw_match.group(1) if sw_match else "N/A"

    _, ufs_res = execute_normal_command(ssh, "cat /firmware/verinfo/ver_info.txt", env_fix, log_func, status_update_func)
    ufs = "N/A"
    try:
        v_json = json.loads(ufs_res)
        v_str = v_json.get("Version", "")
        ufs_match = re.search(r'_(R\d+_[A-Z0-9_]+)-', v_str)
        if ufs_match:
            ufs = ufs_match.group(1)
    except Exception:
        pass
    return {"mcu": mcu, "ufs": ufs, "switch": switch}


def verify_versions(ssh: paramiko.SSHClient, env_fix: str, expected: Dict[str, str],
                    stage_name: str, log_func: Callable, status_update_func: Callable) -> bool:
    log_func("\n" + "=" * 50 + f"\n开始最终版本验证 ({stage_name})\n" + "=" * 50)
    actual = parse_versions(ssh, env_fix, log_func, status_update_func)
    checklist = [
        ("MCU", expected.get("mcu"), actual["mcu"]),
        ("UFS", expected.get("ufs"), actual["ufs"]),
        ("Switch", expected.get("switch"), actual["switch"])
    ]
    log_func(f"\n{'组件':<8} | {'预期版本':<14} | {'实际版本':<14} | {'状态'}")
    log_func("-" * 65)
    all_ok = True
    for name, exp, real in checklist:
        if exp is None:
            match = True
            status = "⚠️ 未校验"
        else:
            match = (str(exp) == str(real))
            if not match:
                all_ok = False
            status = '✔' if match else '✘ FAIL'
        log_func(f"{name:<10} | {str(exp):<18} | {str(real):<18} | {status}")
    return all_ok


# ========================= 具体升级流程 =========================

def run_full_upgrade(config: Dict, log_func: Callable, status_update_func: Callable):
    dev = config['device']
    paths = config['paths']
    expected = config.get('expected_versions', {})
    host, user, pw = dev['host'], dev['user'], dev['pw']
    BIN_PATH = paths.get('bin_path', "/mnt/bin")
    LIB_PATHS = paths.get('lib_paths', "/mnt/lib64:/mnt/usr/lib64:/lib:/usr/lib")
    REMOTE_OTA_DIR = paths.get('remote_ota_dir', "/ota")
    LOCAL_IMAGE_DIR = paths.get('local_image_dir', "./images")
    files = ["MCU_GTMC_AY5_T1_Merge_Boot_App.hex", "sail_ota.bin", "bcm89572_evk_avb_switch_rev1.img", "ufs_ota.bin"]

    update_cmds_1 = [
        "ota_tool show-version",
        "switch_bcm_flasher -v",
        "cat /firmware/verinfo/ver_info.txt",
        f"ota_tool update-mcu {REMOTE_OTA_DIR}/MCU_GTMC_AY5_T1_Merge_Boot_App.hex",
        "ota_proxy_app b",
        f"ota_proxy_app j {REMOTE_OTA_DIR}/ufs_ota.bin",
        "ota_proxy_app s",
        f"ota_proxy_app k {REMOTE_OTA_DIR}/sail_ota.bin",
        f"switch_bcm_flasher -f {REMOTE_OTA_DIR}/bcm89572_evk_avb_switch_rev1.img",
        "ota_tool reset-mcu-ota"
    ]
    update_cmds_2 = [
        f"ota_tool update-mcu {REMOTE_OTA_DIR}/MCU_GTMC_AY5_T1_Merge_Boot_App.hex",
        "ota_proxy_app b",
        f"ota_proxy_app j {REMOTE_OTA_DIR}/ufs_ota.bin",
        "ota_proxy_app s",
        f"ota_proxy_app k {REMOTE_OTA_DIR}/sail_ota.bin",
        "ota_tool reset-mcu-ota"
    ]
    env_fix = (
        f"export PATH=$PATH:{BIN_PATH}; "
        f"export LD_LIBRARY_PATH={LIB_PATHS}:$LD_LIBRARY_PATH; "
        "[ -f /etc/profile ] && . /etc/profile; "
    )

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    log_func(f"[*] 正在连接设备: {host}...")
    ssh.connect(host, username=user, password=pw, timeout=15)

    try:
        upload_files(ssh, REMOTE_OTA_DIR, LOCAL_IMAGE_DIR, files, log_func, status_update_func)

        log_func("\n" + "=" * 50 + "\n开始执行: 远程第一面升级序列\n" + "=" * 50)
        for cmd in update_cmds_1:
            if "reset-mcu-ota" in cmd:
                execute_reboot_command(ssh, cmd, env_fix, log_func)
            else:
                verbose_mode = not ("update-mcu" in cmd or "switch-partition" in cmd)
                code, _ = execute_normal_command(ssh, cmd, env_fix, log_func, status_update_func, verbose=verbose_mode)
                if code != 0:
                    raise RuntimeError(f"命令 {cmd} 执行失败")
        ssh.close()
        ssh = wait_reboot_and_reconnect(host, user, pw, log_func, wait_seconds=30, retries=5)

        code_m, _ = execute_normal_command(ssh, "ota_proxy_app m", env_fix, log_func, status_update_func, timeout=120)
        if code_m != 0:
            log_func("[警告] ota_proxy_app m 返回非零，继续进行版本校验...")
        time.sleep(5)
        ok_1 = verify_versions(ssh, env_fix, expected, "第一遍后", log_func, status_update_func)

        upload_files(ssh, REMOTE_OTA_DIR, LOCAL_IMAGE_DIR, files, log_func, status_update_func)
        log_func("\n" + "=" * 50 + "\n开始执行: 远程第二面升级序列\n" + "=" * 50)
        for cmd in update_cmds_2:
            if "reset-mcu-ota" in cmd:
                execute_reboot_command(ssh, cmd, env_fix, log_func)
            else:
                verbose_mode = not ("update-mcu" in cmd or "switch-partition" in cmd)
                code, _ = execute_normal_command(ssh, cmd, env_fix, log_func, status_update_func, verbose=verbose_mode)
                if code != 0:
                    raise RuntimeError(f"命令 {cmd} 执行失败")
        ssh.close()
        ssh = wait_reboot_and_reconnect(host, user, pw, log_func, wait_seconds=30, retries=5)

        code_m, _ = execute_normal_command(ssh, "ota_proxy_app m", env_fix, log_func, status_update_func, timeout=120)
        if code_m != 0:
            log_func("[警告] ota_proxy_app m 返回非零...")
        time.sleep(5)
        ok_2 = verify_versions(ssh, env_fix, expected, "第二遍后", log_func, status_update_func)

        if ok_1 and ok_2:
            log_func("\n" + "=" * 50 + "\n√ 一键升级及验证流程圆满完成！\n" + "=" * 50)
        else:
            raise RuntimeError("升级完成但版本号不匹配！")
    finally:
        if ssh.get_transport() and ssh.get_transport().is_active():
            log_func("\n" + "-" * 30 + "\n[*] 启动环境清理程序...")
            for f in files:
                remote_path = f"{REMOTE_OTA_DIR}/{f}"
                cmd = f"if [ -f {remote_path} ]; then rm -f {remote_path} && echo '已删除: {f}'; fi"
                execute_normal_command(ssh, cmd, env_fix, log_func, status_update_func)
            ssh.close()
        status_update_func("就绪")


def check_versions_after_reboot(host, user, pw, env_fix, expected, stage_name, log_func, status_update_func):
    ssh = wait_reboot_and_reconnect(host, user, pw, log_func, wait_seconds=30, retries=5)
    all_ok = verify_versions(ssh, env_fix, expected, stage_name, log_func, status_update_func)
    return ssh, all_ok


def run_mcu_upgrade(config: Dict, log_func: Callable, status_update_func: Callable):
    """单独升级 MCU - 静默模式：不在日志区域显示命令实时输出，仅显示关键步骤和进度条"""
    dev = config['device']
    paths = config['paths']
    expected = config.get('expected_versions', {})
    host, user, pw = dev['host'], dev['user'], dev['pw']
    BIN_PATH = paths.get('bin_path', "/mnt/bin")
    LIB_PATHS = paths.get('lib_paths', "/mnt/lib64:/mnt/usr/lib64:/lib:/usr/lib")
    REMOTE_OTA_DIR = paths.get('remote_ota_dir', "/ota")
    LOCAL_IMAGE_DIR = paths.get('local_image_dir', "./images")
    files = ["MCU_GTMC_AY5_T1_Merge_Boot_App.hex"]

    env_fix = (
        f"export PATH=$PATH:{BIN_PATH}; "
        f"export LD_LIBRARY_PATH={LIB_PATHS}:$LD_LIBRARY_PATH; "
        "[ -f /etc/profile ] && . /etc/profile; "
    )

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    log_func(f"[*] 正在连接设备: {host}...")
    ssh.connect(host, username=user, password=pw, timeout=15)

    try:
        upload_files(ssh, REMOTE_OTA_DIR, LOCAL_IMAGE_DIR, files, log_func, status_update_func)

        # 第一面升级（静默模式，不打印详细输出）
        log_func("\n=== 升级第一面 MCU ===")
        execute_normal_command(ssh, f"ota_tool update-mcu {REMOTE_OTA_DIR}/MCU_GTMC_AY5_T1_Merge_Boot_App.hex", env_fix,
                               log_func, status_update_func, verbose=False)
        execute_reboot_command(ssh, "ota_tool reset-mcu-ota", env_fix, log_func)
        ssh.close()

        ssh, ok1 = check_versions_after_reboot(host, user, pw, env_fix, expected,
                                               "MCU 第一面升级后", log_func, status_update_func)
        if not ok1:
            log_func("[警告] 第一面 MCU 升级后版本不匹配预期，继续第二面升级")

        # 第二面升级（同样静默）
        log_func("\n=== 升级第二面 MCU ===")
        execute_normal_command(ssh, f"ota_tool update-mcu {REMOTE_OTA_DIR}/MCU_GTMC_AY5_T1_Merge_Boot_App.hex", env_fix,
                               log_func, status_update_func, verbose=False)
        execute_reboot_command(ssh, "ota_tool reset-mcu-ota", env_fix, log_func)
        ssh.close()

        ssh, ok2 = check_versions_after_reboot(host, user, pw, env_fix, expected,
                                               "MCU 第二面升级后", log_func, status_update_func)
        if ok1 and ok2:
            log_func("\n[✔] MCU 单独升级完成，版本校验通过")
        else:
            log_func("\n[⚠️] MCU 升级完成但版本校验未完全通过，请检查日志")
    finally:
        if ssh.get_transport() and ssh.get_transport().is_active():
            ssh.close()
        status_update_func("就绪")


def run_switch_upgrade(config: Dict, log_func: Callable, status_update_func: Callable):
    dev = config['device']
    paths = config['paths']
    expected = config.get('expected_versions', {})
    host, user, pw = dev['host'], dev['user'], dev['pw']
    BIN_PATH = paths.get('bin_path', "/mnt/bin")
    LIB_PATHS = paths.get('lib_paths', "/mnt/lib64:/mnt/usr/lib64:/lib:/usr/lib")
    REMOTE_OTA_DIR = paths.get('remote_ota_dir', "/ota")
    LOCAL_IMAGE_DIR = paths.get('local_image_dir', "./images")
    files = ["bcm89572_evk_avb_switch_rev1.img"]

    env_fix = (
        f"export PATH=$PATH:{BIN_PATH}; "
        f"export LD_LIBRARY_PATH={LIB_PATHS}:$LD_LIBRARY_PATH; "
        "[ -f /etc/profile ] && . /etc/profile; "
    )

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    log_func(f"[*] 正在连接设备: {host}...")
    ssh.connect(host, username=user, password=pw, timeout=15)

    try:
        upload_files(ssh, REMOTE_OTA_DIR, LOCAL_IMAGE_DIR, files, log_func, status_update_func)
        execute_normal_command(ssh, f"switch_bcm_flasher -f {REMOTE_OTA_DIR}/bcm89572_evk_avb_switch_rev1.img", env_fix,
                               log_func, status_update_func)
        execute_reboot_command(ssh, "ota_tool reset-mcu-ota", env_fix, log_func)
        ssh.close()

        ssh, ok = check_versions_after_reboot(host, user, pw, env_fix, expected,
                                              "Switch 升级后", log_func, status_update_func)
        if ok:
            log_func("\n[✔] Switch 单独升级完成，版本校验通过")
        else:
            log_func("\n[⚠️] Switch 升级完成但版本校验未通过，请检查日志")
    finally:
        if ssh.get_transport() and ssh.get_transport().is_active():
            ssh.close()
        status_update_func("就绪")


def run_soc_upgrade(config: Dict, log_func: Callable, status_update_func: Callable):
    dev = config['device']
    paths = config['paths']
    expected = config.get('expected_versions', {})
    host, user, pw = dev['host'], dev['user'], dev['pw']
    BIN_PATH = paths.get('bin_path', "/mnt/bin")
    LIB_PATHS = paths.get('lib_paths', "/mnt/lib64:/mnt/usr/lib64:/lib:/usr/lib")
    REMOTE_OTA_DIR = paths.get('remote_ota_dir', "/ota")
    LOCAL_IMAGE_DIR = paths.get('local_image_dir', "./images")
    files = ["sail_ota.bin", "ufs_ota.bin"]

    env_fix = (
        f"export PATH=$PATH:{BIN_PATH}; "
        f"export LD_LIBRARY_PATH={LIB_PATHS}:$LD_LIBRARY_PATH; "
        "[ -f /etc/profile ] && . /etc/profile; "
    )

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    log_func(f"[*] 正在连接设备: {host}...")
    ssh.connect(host, username=user, password=pw, timeout=15)

    try:
        upload_files(ssh, REMOTE_OTA_DIR, LOCAL_IMAGE_DIR, files, log_func, status_update_func)

        log_func("\n=== 第一遍 SoC 升级 ===")
        execute_normal_command(ssh, "ota_proxy_app b", env_fix, log_func, status_update_func)
        execute_normal_command(ssh, f"ota_proxy_app j {REMOTE_OTA_DIR}/ufs_ota.bin", env_fix, log_func, status_update_func)
        execute_normal_command(ssh, "ota_proxy_app s", env_fix, log_func, status_update_func)
        execute_normal_command(ssh, f"ota_proxy_app k {REMOTE_OTA_DIR}/sail_ota.bin", env_fix, log_func, status_update_func)
        execute_reboot_command(ssh, "ota_tool reset-mcu-ota", env_fix, log_func)
        ssh.close()

        ssh, ok1 = check_versions_after_reboot(host, user, pw, env_fix, expected,
                                               "SoC 第一遍升级后", log_func, status_update_func)

        execute_normal_command(ssh, "ota_proxy_app m", env_fix, log_func, status_update_func, timeout=120)

        log_func("\n=== 第二遍 SoC 升级 ===")
        upload_files(ssh, REMOTE_OTA_DIR, LOCAL_IMAGE_DIR, files, log_func, status_update_func)
        execute_normal_command(ssh, "ota_proxy_app b", env_fix, log_func, status_update_func)
        execute_normal_command(ssh, f"ota_proxy_app j {REMOTE_OTA_DIR}/ufs_ota.bin", env_fix, log_func, status_update_func)
        execute_normal_command(ssh, "ota_proxy_app s", env_fix, log_func, status_update_func)
        execute_normal_command(ssh, f"ota_proxy_app k {REMOTE_OTA_DIR}/sail_ota.bin", env_fix, log_func, status_update_func)
        execute_reboot_command(ssh, "ota_tool reset-mcu-ota", env_fix, log_func)
        ssh.close()

        ssh, ok2 = check_versions_after_reboot(host, user, pw, env_fix, expected,
                                               "SoC 第二遍升级后", log_func, status_update_func)

        execute_normal_command(ssh, "ota_proxy_app m", env_fix, log_func, status_update_func, timeout=120)

        if ok1 and ok2:
            log_func("\n[✔] SoC 单独升级完成，版本校验通过")
        else:
            log_func("\n[⚠️] SoC 升级完成但版本校验未完全通过，请检查日志")
    finally:
        if ssh.get_transport() and ssh.get_transport().is_active():
            ssh.close()
        status_update_func("就绪")


# ========================= GUI 应用 =========================

class OTAUpgradeApp:
    def __init__(self, root):
        self.root = root
        self.root.title("OTA 升级工具")
        self.root.geometry("900x700")
        self.root.resizable(True, True)

        style = ttk.Style()
        style.configure("TButton", font=("微软雅黑", 10), padding=6)
        style.configure("TLabel", font=("微软雅黑", 10))

        try:
            self.config = load_config()
        except Exception as e:
            messagebox.showerror("配置错误", f"无法加载 config.json:\n{e}")
            sys.exit(1)

        main_frame = ttk.Frame(root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        info_frame = ttk.LabelFrame(main_frame, text="设备信息", padding="5")
        info_frame.pack(fill=tk.X, pady=(0,10))
        dev = self.config['device']
        ttk.Label(info_frame, text=f"主机: {dev['host']}").pack(anchor=tk.W)
        ttk.Label(info_frame, text=f"用户: {dev['user']}").pack(anchor=tk.W)

        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(0,10))

        self.btn_full = ttk.Button(btn_frame, text="一键升级 (完整流程)", command=self.start_full_upgrade)
        self.btn_full.pack(side=tk.LEFT, padx=5)

        self.btn_mcu = ttk.Button(btn_frame, text="单独升级 MCU", command=self.start_mcu_upgrade)
        self.btn_mcu.pack(side=tk.LEFT, padx=5)

        self.btn_switch = ttk.Button(btn_frame, text="单独升级 Switch", command=self.start_switch_upgrade)
        self.btn_switch.pack(side=tk.LEFT, padx=5)

        self.btn_soc = ttk.Button(btn_frame, text="单独升级 SoC", command=self.start_soc_upgrade)
        self.btn_soc.pack(side=tk.LEFT, padx=5)

        # 清空日志按钮（放在右侧）
        self.btn_clear_log = ttk.Button(btn_frame, text="清空日志", command=self.clear_log)
        self.btn_clear_log.pack(side=tk.RIGHT, padx=5)

        log_frame = ttk.LabelFrame(main_frame, text="升级日志", padding="5")
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)

        status_frame = ttk.Frame(main_frame)
        status_frame.pack(fill=tk.X, pady=(5,0))
        self.status_label = ttk.Label(status_frame, text="就绪", relief=tk.SUNKEN, anchor=tk.W, font=("微软雅黑", 9))
        self.status_label.pack(fill=tk.X)

        self.progress_var = tk.IntVar()
        self.progress_bar = ttk.Progressbar(main_frame, variable=self.progress_var, mode='indeterminate')
        self.progress_bar.pack(fill=tk.X, pady=(5,0))

        self.worker_thread = None
        self.running = False

    def log(self, message, end='\n'):
        def _append():
            self.log_text.insert(tk.END, message + end)
            self.log_text.see(tk.END)
            self.root.update_idletasks()
        self.root.after(0, _append)

    def clear_log(self):
        """清空日志文本框"""
        self.log_text.delete(1.0, tk.END)

    def update_status(self, text):
        def _update():
            self.status_label.config(text=text)
        self.root.after(0, _update)

    def start_progress(self):
        self.progress_bar.start(10)
        self.set_buttons_state(False)

    def stop_progress(self):
        self.progress_bar.stop()
        self.set_buttons_state(True)

    def set_buttons_state(self, enabled):
        state = tk.NORMAL if enabled else tk.DISABLED
        self.btn_full.config(state=state)
        self.btn_mcu.config(state=state)
        self.btn_switch.config(state=state)
        self.btn_soc.config(state=state)
        self.btn_clear_log.config(state=state)  # 清空日志按钮在任务运行时也可用，但不影响，这里保持同步

    def run_in_thread(self, target_func):
        if self.running:
            messagebox.showwarning("提示", "已有升级任务正在运行，请等待完成")
            return
        self.running = True
        self.start_progress()
        self.log("=" * 60)
        self.log("开始新的升级任务")
        def wrapper():
            try:
                target_func(self.config, self.log, self.update_status)
            except Exception as e:
                self.log(f"\n[致命错误] {e}")
                messagebox.showerror("升级错误", str(e))
            finally:
                self.running = False
                self.stop_progress()
                self.log("\n升级任务结束。")
                self.update_status("就绪")
        self.worker_thread = threading.Thread(target=wrapper, daemon=True)
        self.worker_thread.start()

    def start_full_upgrade(self):
        self.run_in_thread(run_full_upgrade)

    def start_mcu_upgrade(self):
        self.run_in_thread(run_mcu_upgrade)

    def start_switch_upgrade(self):
        self.run_in_thread(run_switch_upgrade)

    def start_soc_upgrade(self):
        self.run_in_thread(run_soc_upgrade)


if __name__ == "__main__":
    root = tk.Tk()
    app = OTAUpgradeApp(root)
    root.mainloop()