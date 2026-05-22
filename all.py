#!/usr/bin/env python3
import paramiko
import os
import sys
import json
import time
import re
import socket
from typing import Dict, List, Tuple, Any

# ========================= 辅助函数 =========================

def load_config() -> Dict[str, Any]:
    """从同级目录加载 config.json"""
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if not os.path.exists(config_path):
        print(f"[错误] 找不到配置文件: {config_path}")
        sys.exit(1)

    with open(config_path, 'r', encoding='utf-8') as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            print(f"[错误] JSON 格式非法: {e}")
            sys.exit(1)


def progress_callback(filename: str, transferred: int, total: int):
    """文件上传进度回调"""
    percent = (transferred / total) * 100
    bar_length = 40
    filled = int(bar_length * transferred // total)
    bar = '█' * filled + '-' * (bar_length - filled)
    sys.stdout.write(f"\r  上传 {filename}: [{bar}] {percent:.1f}% ({transferred}/{total} bytes)")
    sys.stdout.flush()
    if transferred >= total:
        sys.stdout.write("\n")


def execute_normal_command(ssh: paramiko.SSHClient, cmd: str, env_fix: str, timeout: int = 300) -> Tuple[int, str]:
    """
    执行普通命令，持续读取输出避免管道阻塞，返回 (退出码, 输出内容)
    """
    full_cmd = f"{env_fix} {cmd}"
    print(f"\n[执行]: {cmd}")

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
            print(data, end='', flush=True)
        if stderr.channel.recv_stderr_ready():
            data = stderr.channel.recv_stderr(4096).decode('utf-8', errors='ignore')
            err_chunks.append(data)
            if data.strip():
                print(f"[stderr] {data}", end='', flush=True)
        time.sleep(0.05)

    exit_code = stdout.channel.recv_exit_status()
    remaining_out = stdout.read().decode('utf-8', errors='ignore')
    remaining_err = stderr.read().decode('utf-8', errors='ignore')
    out_msg = ''.join(out_chunks) + remaining_out
    err_msg = ''.join(err_chunks) + remaining_err
    out_msg = out_msg.strip()
    err_msg = err_msg.strip()

    if exit_code != 0:
        print(f"[❌ 失败] 退出码: {exit_code}")
        if err_msg:
            print(f"[错误详情]: {err_msg}")
    else:
        print("[✔ 成功]")

    return exit_code, out_msg


def execute_reboot_command(ssh: paramiko.SSHClient, cmd: str, env_fix: str) -> Tuple[int, str]:
    """
    执行重启命令（例如 ota_tool reset-mcu-ota）。
    由于命令执行后设备会立即断开连接，我们忽略所有 socket 异常，并返回成功。
    """
    full_cmd = f"{env_fix} {cmd}"
    print(f"\n[执行]: {cmd}")
    try:
        stdin, stdout, stderr = ssh.exec_command(full_cmd)
        # 尝试等待一小段时间，让命令有机会开始执行
        time.sleep(0.5)
        # 主动关闭连接前，读取可能的部分输出
        out_chunks = []
        if stdout.channel.recv_ready():
            out_chunks.append(stdout.channel.recv(4096).decode('utf-8', errors='ignore'))
        out_msg = ''.join(out_chunks)
        if out_msg:
            print(out_msg)
        # 无论是否抛出异常，都认为重启命令已发送成功
        print("[✔ 重启命令已发送，设备将重启]")
    except (socket.error, paramiko.SSHException, EOFError) as e:
        # 预期的断开异常，忽略
        print(f"[!] 连接已断开（预期行为），重启命令成功。")
        return 0, ""
    except Exception as e:
        print(f"[❌] 执行重启命令时出现未知异常: {e}")
        return -1, ""
    return 0, ""


def upload_zip(ssh: paramiko.SSHClient, remote_dir: str, local_zip_path: str) -> None:
    """上传压缩包到远程目录"""
    sftp = ssh.open_sftp()
    try:
        sftp.mkdir(remote_dir)
    except IOError:
        pass

    print(f"[*] 正在上传压缩包至 {remote_dir} ...")
    remote_zip = os.path.join(remote_dir, os.path.basename(local_zip_path))
    sftp.put(local_zip_path, remote_zip, callback=lambda t, total: progress_callback(os.path.basename(local_zip_path), t, total))
    sftp.close()
    print("[✔] 压缩包上传完成")


def unzip_package(ssh: paramiko.SSHClient, remote_dir: str, zip_filename: str) -> None:
    """在远程目录解压压缩包"""
    print(f"[*] 正在解压 {zip_filename} ...")
    cmd = f"cd {remote_dir} && unzip -o {zip_filename}"
    code, output = execute_normal_command(ssh, cmd, "", timeout=60)
    if code != 0:
        raise RuntimeError(f"解压失败: {output}")
    print("[✔] 解压完成")


def delete_extracted_files(ssh: paramiko.SSHClient, remote_dir: str, files_to_delete: List[str]) -> None:
    """删除解压出的固件文件（保留压缩包）"""
    print(f"[*] 正在删除解压出的固件文件...")
    for f in files_to_delete:
        remote_path = os.path.join(remote_dir, f)
        cmd = f"if [ -f {remote_path} ]; then rm -f {remote_path} && echo '已删除: {f}'; fi"
        execute_normal_command(ssh, cmd, "", timeout=10)
    print("[✔] 解压文件清理完成")


def delete_zip(ssh: paramiko.SSHClient, remote_dir: str, zip_filename: str) -> None:
    """最后删除压缩包"""
    print(f"[*] 正在删除压缩包 {zip_filename} ...")
    remote_path = os.path.join(remote_dir, zip_filename)
    cmd = f"if [ -f {remote_path} ]; then rm -f {remote_path} && echo '已删除压缩包'; fi"
    execute_normal_command(ssh, cmd, "", timeout=10)
    print("[✔] 压缩包已删除")


def execute_commands(ssh: paramiko.SSHClient, env_fix: str, cmd_list: List[str], step_desc: str) -> None:
    """依次执行一组命令，自动识别重启命令并特殊处理"""
    print("\n" + "=" * 50 + f"\n开始执行: {step_desc}\n" + "=" * 50)
    for cmd in cmd_list:
        if "reset-mcu-ota" in cmd:
            code, _ = execute_reboot_command(ssh, cmd, env_fix)
        else:
            code, _ = execute_normal_command(ssh, cmd, env_fix)
        if code != 0:
            print(f"程序因 {cmd} 错误而中止。")
            sys.exit(1)


def wait_reboot_and_reconnect(host: str, user: str, pw: str, wait_seconds: int = 30, retries: int = 5) -> paramiko.SSHClient:
    """等待设备重启，并重新建立 SSH 连接"""
    print(f"\n[*] 已完成复位指令，进入 {wait_seconds}s 硬件重启等待期...")
    time.sleep(wait_seconds)

    print(f"[*] 正在尝试重新连接设备 {host}...")
    for attempt in range(1, retries + 1):
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(host, username=user, password=pw, timeout=15)
            print("[✔] 重连成功！")
            return ssh
        except Exception as e:
            print(f"[!] 等待设备上线中 ({attempt}/{retries})... {e}")
            time.sleep(10)
    print("[❌] 无法重新连接到设备，程序退出。")
    sys.exit(1)


def parse_versions(ssh: paramiko.SSHClient, env_fix: str) -> Dict[str, str]:
    """从设备获取当前各组件版本"""
    _, mcu_res = execute_normal_command(ssh, "ota_tool show-version", env_fix)
    mcu_match = re.search(r'MCU Version:\s*(\S+)', mcu_res)
    mcu = mcu_match.group(1) if mcu_match else "N/A"

    _, sw_res = execute_normal_command(ssh, "switch_bcm_flasher -v", env_fix)
    sw_match = re.search(r'software version\s*:\s*DFN_(\S+)', sw_res)
    switch = sw_match.group(1) if sw_match else "N/A"

    _, ufs_res = execute_normal_command(ssh, "cat /firmware/verinfo/ver_info.txt", env_fix)
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


def verify_versions(ssh: paramiko.SSHClient, env_fix: str, expected: Dict[str, str], stage_name: str) -> bool:
    """版本校验，打印表格"""
    print("\n" + "=" * 50 + f"\n开始最终版本验证 ({stage_name})\n" + "=" * 50)

    actual = parse_versions(ssh, env_fix)

    checklist = [
        ("MCU", expected.get("mcu"), actual["mcu"]),
        ("UFS", expected.get("ufs"), actual["ufs"]),
        ("Switch", expected.get("switch"), actual["switch"])
    ]

    print(f"\n{'组件':<8} | {'预期版本':<14} | {'实际版本':<14} | {'状态'}")
    print("-" * 65)
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
        print(f"{name:<10} | {str(exp):<18} | {str(real):<18} | {status}")
    return all_ok


# ========================= 主流程 =========================

def run_ota_process() -> None:
    config = load_config()

    try:
        dev = config['device']
        paths = config['paths']
        expected = config.get('expected_versions', {})
        zip_filename = config.get('zip_filename')  # 从 config 读取压缩包名
        if not zip_filename:
            print("[错误] config.json 中缺少 zip_filename 字段")
            sys.exit(1)
    except KeyError as e:
        print(f"[错误] 配置文件缺少必要项: {e}")
        sys.exit(1)

    host = dev['host']
    user = dev['user']
    pw = dev['pw']
    BIN_PATH = paths.get('bin_path', "/mnt/bin")
    LIB_PATHS = paths.get('lib_paths', "/mnt/lib64:/mnt/usr/lib64:/lib:/usr/lib")
    REMOTE_OTA_DIR = paths.get('remote_ota_dir', "/ota")
    LOCAL_IMAGE_DIR = paths.get('local_image_dir', "./images")

    # 构建本地压缩包完整路径
    local_zip_path = os.path.join(LOCAL_IMAGE_DIR, zip_filename)
    if not os.path.exists(local_zip_path):
        print(f"[错误] 找不到压缩包: {local_zip_path}")
        sys.exit(1)

    # 解压后需要删除的固件文件列表（根据图中的文件名）
    extracted_files = ["mcu_ota.hex", "sail_ota.bin", "switch_ota.img", "ufs_ota.bin", "ota_firmware_info.xml"]

    update_cmds_1 = [
        "ota_tool show-version",
        "switch_bcm_flasher -v",
        "cat /firmware/verinfo/ver_info.txt",
        f"ota_tool update-mcu {REMOTE_OTA_DIR}/mcu_ota.hex",
        "ota_tool switch-partition",
        "ota_proxy_app b",
        f"ota_proxy_app j {REMOTE_OTA_DIR}/ufs_ota.bin",
        "ota_proxy_app s",
        f"ota_proxy_app k {REMOTE_OTA_DIR}/sail_ota.bin",
        f"switch_bcm_flasher -f {REMOTE_OTA_DIR}/switch_ota.img",
        "ota_tool reset-mcu-ota"
    ]

    update_cmds_2 = [
        f"ota_tool update-mcu {REMOTE_OTA_DIR}/mcu_ota.hex",
        "ota_tool switch-partition",
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

    ssh = None
    try:
        print(f"[*] 正在连接设备: {host}...")
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(host, username=user, password=pw, timeout=15)

        # 上传压缩包（只上传一次）
        upload_zip(ssh, REMOTE_OTA_DIR, local_zip_path)

        # ========== 第一遍升级 ==========
        # 解压
        unzip_package(ssh, REMOTE_OTA_DIR, zip_filename)
        # 执行升级命令
        execute_commands(ssh, env_fix, update_cmds_1, "远程第一面升级序列")
        # 删除解压出的固件文件（保留压缩包）
        delete_extracted_files(ssh, REMOTE_OTA_DIR, extracted_files)

        ssh.close()
        ssh = wait_reboot_and_reconnect(host, user, pw, wait_seconds=30, retries=5)

        print("[*] 正在执行 ota_proxy_app m ...")
        code_m, _ = execute_normal_command(ssh, "ota_proxy_app m", env_fix, timeout=120)
        if code_m != 0:
            print("[警告] ota_proxy_app m 返回了非零退出码，继续进行版本校验...")
        time.sleep(5)

        ok_1 = verify_versions(ssh, env_fix, expected, "第一遍后")

        # ========== 第二遍升级 ==========
        # 再次解压（因为之前删除了文件）
        unzip_package(ssh, REMOTE_OTA_DIR, zip_filename)
        execute_commands(ssh, env_fix, update_cmds_2, "远程第二面升级序列")
        # 再次删除解压文件
        delete_extracted_files(ssh, REMOTE_OTA_DIR, extracted_files)

        ssh.close()
        ssh = wait_reboot_and_reconnect(host, user, pw, wait_seconds=30, retries=5)

        print("[*] 正在执行 ota_proxy_app m ...")
        code_m, _ = execute_normal_command(ssh, "ota_proxy_app m", env_fix, timeout=120)
        if code_m != 0:
            print("[警告] ota_proxy_app m 返回了非零退出码，继续进行版本校验...")
        time.sleep(5)

        ok_2 = verify_versions(ssh, env_fix, expected, "第二遍后")

        # 最终删除压缩包
        delete_zip(ssh, REMOTE_OTA_DIR, zip_filename)

        if ok_1 and ok_2:
            print("\n" + "=" * 50 + "\n√ 升级及验证流程圆满完成！\n" + "=" * 50)
        else:
            print("\n" + "!" * 50 + "\n❌ 警告：升级完成但版本号不匹配！\n" + "!" * 50)
            sys.exit(1)

    except Exception as e:
        print(f"\n[致命异常]: {e}")
        sys.exit(1)
    finally:
        if ssh is not None and ssh.get_transport() and ssh.get_transport().is_active():
            # 最后再次确保清理（如果之前未删除）
            try:
                delete_extracted_files(ssh, REMOTE_OTA_DIR, extracted_files)
                delete_zip(ssh, REMOTE_OTA_DIR, zip_filename)
            except:
                pass
            ssh.close()


if __name__ == "__main__":
    run_ota_process()