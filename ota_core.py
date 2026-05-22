"""OTA 升级核心逻辑模块。

提供细粒度 SSH/SFTP 操作函数，以及四种升级流程函数。
所有函数通过 callbacks dict 解耦 UI 层。
"""
import paramiko
import os
import json
import time
import re
import socket
from typing import Dict, List, Tuple, Any, Callable, Optional


# ==================== 配置管理 ====================

def load_config(path: str = "config/config.json") -> Dict[str, Any]:
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"找不到配置文件: {config_path}")
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_config(config: Dict[str, Any], path: str = "config/config.json") -> None:
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4, ensure_ascii=False)


# ==================== SSH 操作 ====================

def ssh_connect(host: str, user: str, pw: str, timeout: int = 15) -> paramiko.SSHClient:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, username=user, password=pw, timeout=timeout)
    return ssh


def ssh_disconnect(ssh: paramiko.SSHClient) -> None:
    if ssh and ssh.get_transport() and ssh.get_transport().is_active():
        ssh.close()


def ssh_test_connection(host: str, user: str, pw: str) -> Tuple[bool, str]:
    try:
        ssh = ssh_connect(host, user, pw, timeout=15)
        ssh_disconnect(ssh)
        return True, ""
    except Exception as e:
        return False, str(e)


# ==================== 环境变量 ====================

def build_env_fix(config: Dict[str, Any]) -> str:
    paths = config.get('paths', {})
    bin_path = paths.get('bin_path', "/mnt/bin")
    lib_paths = paths.get('lib_paths', "/mnt/lib64:/mnt/usr/lib64:/lib:/usr/lib")
    return (
        f"export PATH=$PATH:{bin_path}; "
        f"export LD_LIBRARY_PATH={lib_paths}:$LD_LIBRARY_PATH; "
        "[ -f /etc/profile ] && . /etc/profile; "
    )


# ==================== 文件上传 ====================

def upload_file(ssh: paramiko.SSHClient, local_path: str, remote_path: str,
                progress_callback: Optional[Callable] = None) -> None:
    sftp = ssh.open_sftp()
    try:
        remote_dir = os.path.dirname(remote_path)
        try:
            sftp.mkdir(remote_dir)
        except IOError:
            pass
        filename = os.path.basename(local_path)
        sftp.put(local_path, remote_path,
                 callback=lambda t, total: progress_callback(filename, t, total) if progress_callback else None)
    finally:
        sftp.close()


def upload_files_batch(ssh: paramiko.SSHClient, remote_dir: str, local_dir: str,
                       files: List[str], progress_callback: Optional[Callable] = None,
                       log_callback: Optional[Callable] = None) -> None:
    sftp = ssh.open_sftp()
    try:
        try:
            sftp.mkdir(remote_dir)
        except IOError:
            pass
        if log_callback:
            log_callback(f"[*] 正在同步本地文件至 {remote_dir} ...")
        for f in files:
            local_path = os.path.join(local_dir, f)
            if not os.path.exists(local_path):
                raise FileNotFoundError(f"找不到文件: {local_path}")
            remote_path = f"{remote_dir}/{f}"
            sftp.put(local_path, remote_path,
                     callback=lambda t, total, name=f: progress_callback(name, t, total) if progress_callback else None)
        if log_callback:
            log_callback("[✔] 所有文件上传完成")
    finally:
        sftp.close()


def upload_zip_file(ssh: paramiko.SSHClient, remote_dir: str, local_zip_path: str,
                    progress_callback: Optional[Callable] = None,
                    log_callback: Optional[Callable] = None) -> str:
    sftp = ssh.open_sftp()
    try:
        try:
            sftp.mkdir(remote_dir)
        except IOError:
            pass
        zip_filename = os.path.basename(local_zip_path)
        remote_zip = f"{remote_dir}/{zip_filename}"
        if log_callback:
            log_callback(f"[*] 正在上传压缩包至 {remote_dir} ...")
        sftp.put(local_zip_path, remote_zip,
                 callback=lambda t, total: progress_callback(zip_filename, t, total) if progress_callback else None)
        if log_callback:
            log_callback("[✔] 压缩包上传完成")
        return zip_filename
    finally:
        sftp.close()


# ==================== 远程命令执行 ====================

def execute_command(ssh: paramiko.SSHClient, cmd: str, env_fix: str,
                    timeout: int = 300, verbose: bool = True,
                    log_callback: Optional[Callable] = None,
                    status_callback: Optional[Callable] = None) -> Tuple[int, str]:
    full_cmd = f"{env_fix} {cmd}"
    if log_callback:
        log_callback(f"\n[执行]: {cmd}")
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
            if verbose and log_callback:
                log_callback(data, end='')
            if '%' in data and status_callback:
                for line in data.splitlines():
                    if '%' in line:
                        status_callback(line.strip()[:80])
        if stderr.channel.recv_stderr_ready():
            data = stderr.channel.recv_stderr(4096).decode('utf-8', errors='ignore')
            err_chunks.append(data)
            if data.strip() and verbose and log_callback:
                log_callback(f"[stderr] {data}", end='')
        time.sleep(0.05)

    exit_code = stdout.channel.recv_exit_status()
    remaining_out = stdout.read().decode('utf-8', errors='ignore')
    remaining_err = stderr.read().decode('utf-8', errors='ignore')
    out_msg = ''.join(out_chunks) + remaining_out
    err_msg = ''.join(err_chunks) + remaining_err
    out_msg = out_msg.strip()
    err_msg = err_msg.strip()

    if log_callback:
        if exit_code != 0:
            log_callback(f"[❌ 失败] 退出码: {exit_code}")
            if err_msg:
                log_callback(f"[错误详情]: {err_msg}")
        else:
            log_callback("[✔ 成功]")
    return exit_code, out_msg


def execute_reboot_command(ssh: paramiko.SSHClient, cmd: str, env_fix: str,
                           log_callback: Optional[Callable] = None) -> Tuple[int, str]:
    full_cmd = f"{env_fix} {cmd}"
    if log_callback:
        log_callback(f"\n[执行]: {cmd}")
    try:
        stdin, stdout, stderr = ssh.exec_command(full_cmd)
        time.sleep(0.5)
        out_chunks = []
        if stdout.channel.recv_ready():
            out_chunks.append(stdout.channel.recv(4096).decode('utf-8', errors='ignore'))
        out_msg = ''.join(out_chunks)
        if out_msg and log_callback:
            log_callback(out_msg)
        if log_callback:
            log_callback("[✔ 重启命令已发送，设备将重启]")
    except (socket.error, paramiko.SSHException, EOFError) as e:
        if log_callback:
            log_callback(f"[!] 连接已断开（预期行为），重启命令成功。")
        return 0, ""
    except Exception as e:
        if log_callback:
            log_callback(f"[❌] 执行重启命令时出现未知异常: {e}")
        return -1, ""
    return 0, ""


# ==================== 远程文件操作 ====================

def extract_zip(ssh: paramiko.SSHClient, remote_dir: str, zip_filename: str,
                timeout: int = 60,
                log_callback: Optional[Callable] = None,
                status_callback: Optional[Callable] = None) -> Tuple[int, str]:
    if log_callback:
        log_callback(f"[*] 正在解压 {zip_filename} ...")
    cmd = f"cd {remote_dir} && unzip -o {zip_filename}"
    code, output = execute_command(ssh, cmd, "", timeout=timeout,
                                   log_callback=log_callback, status_callback=status_callback)
    if code != 0:
        raise RuntimeError(f"解压失败: {output}")
    if log_callback:
        log_callback("[✔] 解压完成")
    return code, output


def delete_remote_files(ssh: paramiko.SSHClient, remote_dir: str, filenames: List[str],
                        log_callback: Optional[Callable] = None,
                        status_callback: Optional[Callable] = None) -> None:
    for f in filenames:
        remote_path = f"{remote_dir}/{f}"
        cmd = f"if [ -f {remote_path} ]; then rm -f {remote_path} && echo '已删除: {f}'; fi"
        execute_command(ssh, cmd, "", timeout=10, log_callback=log_callback, status_callback=status_callback)


# ==================== 重连 ====================

def wait_reconnect(host: str, user: str, pw: str, wait_seconds: int = 30, retries: int = 5,
                   log_callback: Optional[Callable] = None) -> paramiko.SSHClient:
    if log_callback:
        log_callback(f"\n[*] 已完成复位指令，进入 {wait_seconds}s 硬件重启等待期...")
    time.sleep(wait_seconds)
    if log_callback:
        log_callback(f"[*] 正在尝试重新连接设备 {host}...")
    for attempt in range(1, retries + 1):
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(host, username=user, password=pw, timeout=15)
            if log_callback:
                log_callback("[✔] 重连成功！")
            return ssh
        except Exception as e:
            if log_callback:
                log_callback(f"[!] 等待设备上线中 ({attempt}/{retries})... {e}")
            time.sleep(10)
    raise RuntimeError("无法重新连接到设备")


# ==================== 版本解析 ====================

def parse_versions(ssh: paramiko.SSHClient, env_fix: str,
                   log_callback: Optional[Callable] = None,
                   status_callback: Optional[Callable] = None) -> Dict[str, str]:
    _, mcu_res = execute_command(ssh, "ota_tool show-version", env_fix,
                                 log_callback=log_callback, status_callback=status_callback)
    mcu_match = re.search(r'MCU Version:\s*(\S+)', mcu_res)
    mcu = mcu_match.group(1) if mcu_match else "N/A"

    _, sw_res = execute_command(ssh, "switch_bcm_flasher -v", env_fix,
                                log_callback=log_callback, status_callback=status_callback)
    sw_match = re.search(r'software version\s*:\s*GTMC_(\S+)', sw_res)
    switch = sw_match.group(1) if sw_match else "N/A"

    _, ufs_res = execute_command(ssh, "cat /firmware/verinfo/ver_info.txt", env_fix,
                                 log_callback=log_callback, status_callback=status_callback)
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
                    stage_name: str,
                    log_callback: Optional[Callable] = None,
                    status_callback: Optional[Callable] = None) -> bool:
    if log_callback:
        log_callback("\n" + "=" * 50 + f"\n开始最终版本验证 ({stage_name})\n" + "=" * 50)
    actual = parse_versions(ssh, env_fix, log_callback=log_callback, status_callback=status_callback)
    checklist = [
        ("MCU", expected.get("mcu"), actual["mcu"]),
        ("UFS", expected.get("ufs"), actual["ufs"]),
        ("Switch", expected.get("switch"), actual["switch"])
    ]
    if log_callback:
        log_callback(f"\n{'组件':<8} | {'预期版本':<14} | {'实际版本':<14} | {'状态'}")
        log_callback("-" * 65)
    all_ok = True
    for name, exp, real in checklist:
        if exp is None:
            match = True
            status_str = "⚠️ 未校验"
        else:
            match = (str(exp) == str(real))
            if not match:
                all_ok = False
            status_str = '✔' if match else '✘ FAIL'
        if log_callback:
            log_callback(f"{name:<10} | {str(exp):<18} | {str(real):<18} | {status_str}")
    return all_ok


# ==================== 辅助 ====================

def _ensure_callbacks(callbacks: Dict[str, Any]) -> Dict[str, Any]:
    """补全 callbacks 中缺失的项为 no-op。"""
    defaults = {
        "log": lambda msg, end='\n': None,
        "status": lambda text: None,
        "progress": lambda name, transferred, total: None,
    }
    for k, v in defaults.items():
        if k not in callbacks:
            callbacks[k] = v
    return callbacks


def _zip_filename_for_role(extracted_files: Dict[str, str], role: str) -> str:
    """从 extracted_files 字典中获取指定 role 对应的文件名。"""
    return extracted_files.get(role, "")


# ==================== 升级流程 ====================

def _do_upload_step(ssh, config, remote_dir, local_dir, files, callbacks, zip_mode, extracted_files):
    """执行上传步骤（direct 模式上传文件列表，zip 模式上传并解压）。"""
    log = callbacks["log"]
    status = callbacks["status"]
    progress = callbacks["progress"]

    if zip_mode:
        zip_path = config.get("zip_file", "")
        if not zip_path or not os.path.exists(zip_path):
            raise FileNotFoundError(f"找不到压缩包: {zip_path}")
        zip_filename = upload_zip_file(ssh, remote_dir, zip_path,
                                       progress_callback=progress, log_callback=log)
        extract_zip(ssh, remote_dir, zip_filename, log_callback=log, status_callback=status)
    else:
        upload_files_batch(ssh, remote_dir, local_dir, files,
                           progress_callback=progress, log_callback=log)
        status("就绪")


def _do_cleanup_step(ssh, config, remote_dir, files, callbacks, zip_mode):
    """执行清理步骤。"""
    log = callbacks["log"]
    status = callbacks["status"]

    if zip_mode:
        zip_path = config.get("zip_file", "")
        zip_filename = os.path.basename(zip_path) if zip_path else ""
        if zip_filename:
            log("\n" + "-" * 30 + "\n[*] 启动环境清理程序...")
            delete_remote_files(ssh, remote_dir, [zip_filename],
                                log_callback=log, status_callback=status)
    else:
        log("\n" + "-" * 30 + "\n[*] 启动环境清理程序...")
        for f in files:
            cmd = f"if [ -f {remote_dir}/{f} ]; then rm -f {remote_dir}/{f} && echo '已删除: {f}'; fi"
            execute_command(ssh, cmd, "", log_callback=log, status_callback=status)


def run_full_upgrade(config: Dict[str, Any], callbacks: Dict[str, Any]) -> None:
    """一键完整升级（遵循 ota_alone.py run_full_upgrade 原始逻辑）。"""
    callbacks = _ensure_callbacks(callbacks)
    log = callbacks["log"]
    status = callbacks["status"]
    progress = callbacks["progress"]

    dev = config['device']
    paths = config['paths']
    expected = config.get('expected_versions', {})
    host, user, pw = dev['host'], dev['user'], dev['pw']
    remote_dir = paths.get('remote_ota_dir', "/ota")
    local_dir = paths.get('local_image_dir', "./images")
    env_fix = build_env_fix(config)
    zip_mode = config.get('mode', 'direct') == 'zip'
    extracted = config.get('extracted_files', {})

    # direct 模式的文件名
    fw = config.get('firmware_files', {})
    mcu_file = fw.get('mcu', 'MCU_GTMC_AY5_T1_Merge_Boot_App.hex')
    sail_file = fw.get('sail', 'sail_ota.bin')
    switch_file = fw.get('switch', 'bcm89572_evk_avb_switch_rev1.img')
    ufs_file = fw.get('ufs', 'ufs_ota.bin')

    # 实际执行命令时引用的文件名（zip 模式用 extracted 中的名字）
    if zip_mode:
        mcu_name = _zip_filename_for_role(extracted, 'mcu') or mcu_file
        sail_name = _zip_filename_for_role(extracted, 'sail') or sail_file
        switch_name = _zip_filename_for_role(extracted, 'switch') or switch_file
        ufs_name = _zip_filename_for_role(extracted, 'ufs') or ufs_file
        upload_files_list = []
        cleanup_files_list = list(extracted.values())
    else:
        mcu_name = mcu_file
        sail_name = sail_file
        switch_name = switch_file
        ufs_name = ufs_file
        upload_files_list = [mcu_file, sail_file, switch_file, ufs_file]
        cleanup_files_list = upload_files_list

    update_cmds_1 = [
        "ota_tool show-version",
        "switch_bcm_flasher -v",
        "cat /firmware/verinfo/ver_info.txt",
        f"ota_tool update-mcu {remote_dir}/{mcu_name}",
        "ota_proxy_app b",
        f"ota_proxy_app j {remote_dir}/{ufs_name}",
        "ota_proxy_app s",
        f"ota_proxy_app k {remote_dir}/{sail_name}",
        f"switch_bcm_flasher -f {remote_dir}/{switch_name}",
        "ota_tool reset-mcu-ota"
    ]
    update_cmds_2 = [
        f"ota_tool update-mcu {remote_dir}/{mcu_name}",
        "ota_proxy_app b",
        f"ota_proxy_app j {remote_dir}/{ufs_name}",
        "ota_proxy_app s",
        f"ota_proxy_app k {remote_dir}/{sail_name}",
        "ota_tool reset-mcu-ota"
    ]

    ssh = ssh_connect(host, user, pw)
    try:
        _do_upload_step(ssh, config, remote_dir, local_dir, upload_files_list, callbacks, zip_mode, extracted)

        log("\n" + "=" * 50 + "\n开始执行: 远程第一面升级序列\n" + "=" * 50)
        for cmd in update_cmds_1:
            if "reset-mcu-ota" in cmd:
                execute_reboot_command(ssh, cmd, env_fix, log_callback=log)
            else:
                verbose_mode = not ("update-mcu" in cmd or "switch-partition" in cmd)
                code, _ = execute_command(ssh, cmd, env_fix, log_callback=log, status_callback=status,
                                          verbose=verbose_mode)
                if code != 0:
                    raise RuntimeError(f"命令 {cmd} 执行失败")
        ssh.close()

        if zip_mode and cleanup_files_list:
            ssh = wait_reconnect(host, user, pw, log_callback=log)
            delete_remote_files(ssh, remote_dir, cleanup_files_list, log_callback=log, status_callback=status)
            ssh.close()

        ssh = wait_reconnect(host, user, pw, log_callback=log)

        code_m, _ = execute_command(ssh, "ota_proxy_app m", env_fix, timeout=120,
                                    log_callback=log, status_callback=status)
        if code_m != 0:
            log("[警告] ota_proxy_app m 返回非零，继续进行版本校验...")
        time.sleep(5)
        ok_1 = verify_versions(ssh, env_fix, expected, "第一遍后", log_callback=log, status_callback=status)

        _do_upload_step(ssh, config, remote_dir, local_dir, upload_files_list, callbacks, zip_mode, extracted)

        log("\n" + "=" * 50 + "\n开始执行: 远程第二面升级序列\n" + "=" * 50)
        for cmd in update_cmds_2:
            if "reset-mcu-ota" in cmd:
                execute_reboot_command(ssh, cmd, env_fix, log_callback=log)
            else:
                verbose_mode = not ("update-mcu" in cmd or "switch-partition" in cmd)
                code, _ = execute_command(ssh, cmd, env_fix, log_callback=log, status_callback=status,
                                          verbose=verbose_mode)
                if code != 0:
                    raise RuntimeError(f"命令 {cmd} 执行失败")
        ssh.close()

        if zip_mode and cleanup_files_list:
            ssh = wait_reconnect(host, user, pw, log_callback=log)
            delete_remote_files(ssh, remote_dir, cleanup_files_list, log_callback=log, status_callback=status)
            ssh.close()

        ssh = wait_reconnect(host, user, pw, log_callback=log)

        code_m, _ = execute_command(ssh, "ota_proxy_app m", env_fix, timeout=120,
                                    log_callback=log, status_callback=status)
        if code_m != 0:
            log("[警告] ota_proxy_app m 返回非零...")
        time.sleep(5)
        ok_2 = verify_versions(ssh, env_fix, expected, "第二遍后", log_callback=log, status_callback=status)

        if ok_1 and ok_2:
            log("\n" + "=" * 50 + "\n√ 一键升级及验证流程圆满完成！\n" + "=" * 50)
        else:
            raise RuntimeError("升级完成但版本号不匹配！")
    finally:
        if ssh.get_transport() and ssh.get_transport().is_active():
            _do_cleanup_step(ssh, config, remote_dir, cleanup_files_list, callbacks, zip_mode)
            ssh.close()
        status("就绪")


def _check_versions_after_reboot(host, user, pw, env_fix, expected, stage_name, callbacks):
    """重连并校验版本，返回 (ssh, all_ok)。"""
    log = callbacks["log"]
    status = callbacks["status"]
    ssh = wait_reconnect(host, user, pw, log_callback=log)
    all_ok = verify_versions(ssh, env_fix, expected, stage_name, log_callback=log, status_callback=status)
    return ssh, all_ok


def run_mcu_upgrade(config: Dict[str, Any], callbacks: Dict[str, Any]) -> None:
    """单独升级 MCU（遵循 ota_alone.py run_mcu_upgrade 原始逻辑）。"""
    callbacks = _ensure_callbacks(callbacks)
    log = callbacks["log"]
    status = callbacks["status"]
    progress = callbacks["progress"]

    dev = config['device']
    paths = config['paths']
    expected = config.get('expected_versions', {})
    host, user, pw = dev['host'], dev['user'], dev['pw']
    remote_dir = paths.get('remote_ota_dir', "/ota")
    local_dir = paths.get('local_image_dir', "./images")
    env_fix = build_env_fix(config)
    zip_mode = config.get('mode', 'direct') == 'zip'
    extracted = config.get('extracted_files', {})

    fw = config.get('firmware_files', {})
    mcu_file = fw.get('mcu', 'MCU_GTMC_AY5_T1_Merge_Boot_App.hex')
    if zip_mode:
        mcu_name = _zip_filename_for_role(extracted, 'mcu') or mcu_file
        upload_files_list = []
        cleanup_files_list = list(extracted.values())
    else:
        mcu_name = mcu_file
        upload_files_list = [mcu_file]
        cleanup_files_list = upload_files_list

    ssh = ssh_connect(host, user, pw)
    try:
        _do_upload_step(ssh, config, remote_dir, local_dir, upload_files_list, callbacks, zip_mode, extracted)

        log("\n=== 升级第一面 MCU ===")
        execute_command(ssh, f"ota_tool update-mcu {remote_dir}/{mcu_name}", env_fix,
                        log_callback=log, status_callback=status, verbose=False)
        execute_reboot_command(ssh, "ota_tool reset-mcu-ota", env_fix, log_callback=log)
        ssh.close()

        if zip_mode and cleanup_files_list:
            ssh = wait_reconnect(host, user, pw, log_callback=log)
            delete_remote_files(ssh, remote_dir, cleanup_files_list, log_callback=log, status_callback=status)
            ssh.close()

        ssh, ok1 = _check_versions_after_reboot(host, user, pw, env_fix, expected, "MCU 第一面升级后", callbacks)
        if not ok1:
            log("[警告] 第一面 MCU 升级后版本不匹配预期，继续第二面升级")

        log("\n=== 升级第二面 MCU ===")
        # zip 模式：第二面需要再次解压
        if zip_mode:
            _do_upload_step(ssh, config, remote_dir, local_dir, upload_files_list, callbacks, zip_mode, extracted)
        execute_command(ssh, f"ota_tool update-mcu {remote_dir}/{mcu_name}", env_fix,
                        log_callback=log, status_callback=status, verbose=False)
        execute_reboot_command(ssh, "ota_tool reset-mcu-ota", env_fix, log_callback=log)
        ssh.close()

        if zip_mode and cleanup_files_list:
            ssh = wait_reconnect(host, user, pw, log_callback=log)
            delete_remote_files(ssh, remote_dir, cleanup_files_list, log_callback=log, status_callback=status)
            ssh.close()

        ssh, ok2 = _check_versions_after_reboot(host, user, pw, env_fix, expected, "MCU 第二面升级后", callbacks)
        if ok1 and ok2:
            log("\n[✔] MCU 单独升级完成，版本校验通过")
        else:
            log("\n[⚠️] MCU 升级完成但版本校验未完全通过，请检查日志")
    finally:
        if ssh.get_transport() and ssh.get_transport().is_active():
            ssh.close()
        status("就绪")


def run_switch_upgrade(config: Dict[str, Any], callbacks: Dict[str, Any]) -> None:
    """单独升级 Switch（遵循 ota_alone.py run_switch_upgrade 原始逻辑）。"""
    callbacks = _ensure_callbacks(callbacks)
    log = callbacks["log"]
    status = callbacks["status"]
    progress = callbacks["progress"]

    dev = config['device']
    paths = config['paths']
    expected = config.get('expected_versions', {})
    host, user, pw = dev['host'], dev['user'], dev['pw']
    remote_dir = paths.get('remote_ota_dir', "/ota")
    local_dir = paths.get('local_image_dir', "./images")
    env_fix = build_env_fix(config)
    zip_mode = config.get('mode', 'direct') == 'zip'
    extracted = config.get('extracted_files', {})

    fw = config.get('firmware_files', {})
    switch_file = fw.get('switch', 'bcm89572_evk_avb_switch_rev1.img')
    if zip_mode:
        switch_name = _zip_filename_for_role(extracted, 'switch') or switch_file
        upload_files_list = []
        cleanup_files_list = list(extracted.values())
    else:
        switch_name = switch_file
        upload_files_list = [switch_file]
        cleanup_files_list = upload_files_list

    ssh = ssh_connect(host, user, pw)
    try:
        _do_upload_step(ssh, config, remote_dir, local_dir, upload_files_list, callbacks, zip_mode, extracted)
        execute_command(ssh, f"switch_bcm_flasher -f {remote_dir}/{switch_name}", env_fix,
                        log_callback=log, status_callback=status)
        execute_reboot_command(ssh, "ota_tool reset-mcu-ota", env_fix, log_callback=log)
        ssh.close()

        if zip_mode and cleanup_files_list:
            ssh = wait_reconnect(host, user, pw, log_callback=log)
            delete_remote_files(ssh, remote_dir, cleanup_files_list, log_callback=log, status_callback=status)
            ssh.close()

        ssh, ok = _check_versions_after_reboot(host, user, pw, env_fix, expected, "Switch 升级后", callbacks)
        if ok:
            log("\n[✔] Switch 单独升级完成，版本校验通过")
        else:
            log("\n[⚠️] Switch 升级完成但版本校验未通过，请检查日志")
    finally:
        if ssh.get_transport() and ssh.get_transport().is_active():
            ssh.close()
        status("就绪")


def run_soc_upgrade(config: Dict[str, Any], callbacks: Dict[str, Any]) -> None:
    """单独升级 SoC（遵循 ota_alone.py run_soc_upgrade 原始逻辑）。"""
    callbacks = _ensure_callbacks(callbacks)
    log = callbacks["log"]
    status = callbacks["status"]
    progress = callbacks["progress"]

    dev = config['device']
    paths = config['paths']
    expected = config.get('expected_versions', {})
    host, user, pw = dev['host'], dev['user'], dev['pw']
    remote_dir = paths.get('remote_ota_dir', "/ota")
    local_dir = paths.get('local_image_dir', "./images")
    env_fix = build_env_fix(config)
    zip_mode = config.get('mode', 'direct') == 'zip'
    extracted = config.get('extracted_files', {})

    fw = config.get('firmware_files', {})
    sail_file = fw.get('sail', 'sail_ota.bin')
    ufs_file = fw.get('ufs', 'ufs_ota.bin')
    if zip_mode:
        sail_name = _zip_filename_for_role(extracted, 'sail') or sail_file
        ufs_name = _zip_filename_for_role(extracted, 'ufs') or ufs_file
        upload_files_list = []
        cleanup_files_list = list(extracted.values())
    else:
        sail_name = sail_file
        ufs_name = ufs_file
        upload_files_list = [sail_file, ufs_file]
        cleanup_files_list = upload_files_list

    ssh = ssh_connect(host, user, pw)
    try:
        _do_upload_step(ssh, config, remote_dir, local_dir, upload_files_list, callbacks, zip_mode, extracted)

        log("\n=== 第一遍 SoC 升级 ===")
        execute_command(ssh, "ota_proxy_app b", env_fix, log_callback=log, status_callback=status)
        execute_command(ssh, f"ota_proxy_app j {remote_dir}/{ufs_name}", env_fix,
                        log_callback=log, status_callback=status)
        execute_command(ssh, "ota_proxy_app s", env_fix, log_callback=log, status_callback=status)
        execute_command(ssh, f"ota_proxy_app k {remote_dir}/{sail_name}", env_fix,
                        log_callback=log, status_callback=status)
        execute_reboot_command(ssh, "ota_tool reset-mcu-ota", env_fix, log_callback=log)
        ssh.close()

        if zip_mode and cleanup_files_list:
            ssh = wait_reconnect(host, user, pw, log_callback=log)
            delete_remote_files(ssh, remote_dir, cleanup_files_list, log_callback=log, status_callback=status)
            ssh.close()

        ssh, ok1 = _check_versions_after_reboot(host, user, pw, env_fix, expected, "SoC 第一遍升级后", callbacks)

        execute_command(ssh, "ota_proxy_app m", env_fix, timeout=120, log_callback=log, status_callback=status)

        log("\n=== 第二遍 SoC 升级 ===")
        _do_upload_step(ssh, config, remote_dir, local_dir, upload_files_list, callbacks, zip_mode, extracted)
        execute_command(ssh, "ota_proxy_app b", env_fix, log_callback=log, status_callback=status)
        execute_command(ssh, f"ota_proxy_app j {remote_dir}/{ufs_name}", env_fix,
                        log_callback=log, status_callback=status)
        execute_command(ssh, "ota_proxy_app s", env_fix, log_callback=log, status_callback=status)
        execute_command(ssh, f"ota_proxy_app k {remote_dir}/{sail_name}", env_fix,
                        log_callback=log, status_callback=status)
        execute_reboot_command(ssh, "ota_tool reset-mcu-ota", env_fix, log_callback=log)
        ssh.close()

        if zip_mode and cleanup_files_list:
            ssh = wait_reconnect(host, user, pw, log_callback=log)
            delete_remote_files(ssh, remote_dir, cleanup_files_list, log_callback=log, status_callback=status)
            ssh.close()

        ssh, ok2 = _check_versions_after_reboot(host, user, pw, env_fix, expected, "SoC 第二遍升级后", callbacks)

        execute_command(ssh, "ota_proxy_app m", env_fix, timeout=120, log_callback=log, status_callback=status)

        if ok1 and ok2:
            log("\n[✔] SoC 单独升级完成，版本校验通过")
        else:
            log("\n[⚠️] SoC 升级完成但版本校验未完全通过，请检查日志")
    finally:
        if ssh.get_transport() and ssh.get_transport().is_active():
            ssh.close()
        status("就绪")
