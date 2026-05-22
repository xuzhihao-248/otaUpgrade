"""OTA 烧录工具 — CLI 模块。

用法: python ota_tool.py --cli [选项]
"""
import os
import sys
import time
import ota_core

UPGRADE_TYPE_ROLES = {
    "full": ["mcu", "sail", "switch", "ufs"],
    "mcu":  ["mcu"],
    "switch": ["switch"],
    "soc":  ["sail", "ufs"],
}

HELP = """OTA 升级工具 — CLI 模式

用法: python ota_tool.py --cli [选项]

选项:
  --host HOST             设备 IP
  --user USER             SSH 用户名
  --pw PW                 SSH 密码
  --bin-path PATH         远程 bin 路径
  --lib-paths PATHS       远程 lib 路径
  --remote-dir DIR        远程 OTA 目录
  --local-dir DIR         本地固件目录
  --expected-mcu VER      预期 MCU 版本
  --expected-ufs VER      预期 UFS 版本
  --expected-switch VER   预期 Switch 版本
  --type TYPE             升级类型: full|mcu|switch|soc (默认: full)
  --source SOURCE         固件来源: direct|zip (默认: direct)
  --files F1 [F2 ...]    direct 模式固件文件路径 (按顺序: mcu sail switch ufs)
  --zip ZIP_PATH          zip 压缩包路径
  --extracted F1 [F2 ...] zip 解压后文件名 (按顺序: mcu sail switch ufs)
  --test-connection       仅测试 SSH 连接
  --help                  显示帮助

所有选项可选，未提供的使用 config.json 中的默认值。
"""


def _parse_args(args):
    """简单命令行参数解析，返回 dict。"""
    parsed = {}
    i = 0
    list_args = {"--files": [], "--extracted": []}

    while i < len(args):
        arg = args[i]
        if arg == "--help":
            print(HELP)
            sys.exit(0)
        elif arg == "--test-connection":
            parsed["test_connection"] = True
            i += 1
        elif arg in ("--files", "--extracted"):
            key = arg
            i += 1
            while i < len(args) and not args[i].startswith("--"):
                list_args[key].append(args[i])
                i += 1
        elif arg.startswith("--") and i + 1 < len(args) and not args[i + 1].startswith("--"):
            parsed[arg] = args[i + 1]
            i += 2
        else:
            print(f"[警告] 忽略无法识别的参数: {arg}")
            i += 1

    parsed["_files"] = list_args["--files"]
    parsed["_extracted"] = list_args["--extracted"]
    return parsed


def _progress_bar(filename, transferred, total):
    percent = (transferred / total) * 100
    bar_length = 40
    filled = int(bar_length * transferred // total)
    bar = '█' * filled + '-' * (bar_length - filled)
    sys.stdout.write(f"\r  上传 {filename}: [{bar}] {percent:.1f}% ({transferred}/{total} bytes)")
    sys.stdout.flush()
    if transferred >= total:
        sys.stdout.write("\n")


def _build_config(cli_args, defaults):
    """合并 CLI 参数与默认配置，返回 run_config。"""
    dev = defaults.get("device", {})
    paths = defaults.get("paths", {})
    ev = defaults.get("expected_versions", {})
    fw = defaults.get("firmware_files", {})
    ext = defaults.get("zip_extracted_files", {})

    upgrade_type = cli_args.get("--type", "full")
    source = cli_args.get("--source", "direct")
    roles = UPGRADE_TYPE_ROLES.get(upgrade_type, UPGRADE_TYPE_ROLES["full"])
    fw_defaults = fw.get(upgrade_type, {})

    config = {
        "device": {
            "host": cli_args.get("--host", dev.get("host", "")),
            "user": cli_args.get("--user", dev.get("user", "")),
            "pw": cli_args.get("--pw", dev.get("pw", "")),
        },
        "paths": {
            "bin_path": cli_args.get("--bin-path", paths.get("bin_path", "")),
            "lib_paths": cli_args.get("--lib-paths", paths.get("lib_paths", "")),
            "remote_ota_dir": cli_args.get("--remote-dir", paths.get("remote_ota_dir", "")),
            "local_image_dir": cli_args.get("--local-dir", paths.get("local_image_dir", "./images")),
        },
        "expected_versions": {
            "mcu": cli_args.get("--expected-mcu", ev.get("mcu", "")),
            "ufs": cli_args.get("--expected-ufs", ev.get("ufs", "")),
            "switch": cli_args.get("--expected-switch", ev.get("switch", "")),
        },
        "mode": source,
        "firmware_files": {},
        "extracted_files": {},
    }

    if source == "zip":
        zip_path = cli_args.get("--zip", "")
        config["zip_file"] = zip_path
        extracted_list = cli_args.get("_extracted", [])
        if extracted_list:
            for idx, role in enumerate(roles):
                if idx < len(extracted_list):
                    config["extracted_files"][role] = extracted_list[idx]
        else:
            for role in roles:
                config["extracted_files"][role] = ext.get(role, fw_defaults.get(role, ""))
    else:
        files_list = cli_args.get("_files", [])
        if files_list:
            for idx, role in enumerate(roles):
                if idx < len(files_list):
                    config["firmware_files"][role] = os.path.basename(files_list[idx])
                    dirname = os.path.dirname(files_list[idx])
                    if dirname:
                        config["paths"]["local_image_dir"] = dirname
        else:
            for role in roles:
                config["firmware_files"][role] = fw_defaults.get(role, "")

    return config


def _print_config(config):
    print("\n" + "=" * 50)
    print("当前配置")
    print("=" * 50)
    dev = config["device"]
    paths = config["paths"]
    ev = config["expected_versions"]
    print(f"  主机: {dev['host']}")
    print(f"  用户: {dev['user']}")
    print(f"  远程目录: {paths['remote_ota_dir']}")
    print(f"  本地目录: {paths['local_image_dir']}")
    print(f"  模式: {config['mode']}")
    print(f"  预期版本: MCU={ev['mcu']}, UFS={ev['ufs']}, Switch={ev['switch']}")
    if config['mode'] == 'zip':
        print(f"  ZIP: {config.get('zip_file', 'N/A')}")
        print(f"  解压文件: {config.get('extracted_files', {})}")
    else:
        print(f"  固件文件: {config.get('firmware_files', {})}")
    print("=" * 50)


def run(args):
    cli_args = _parse_args(args)

    try:
        defaults = ota_core.load_config()
    except Exception as e:
        print(f"[错误] 加载配置失败: {e}")
        sys.exit(1)

    # --test-connection
    if cli_args.get("test_connection"):
        dev = defaults.get("device", {})
        host = cli_args.get("--host", dev.get("host", ""))
        user = cli_args.get("--user", dev.get("user", ""))
        pw = cli_args.get("--pw", dev.get("pw", ""))
        print(f"[*] 测试连接 {host} ...")
        ok, err = ota_core.ssh_test_connection(host, user, pw)
        if ok:
            print("[✔] SSH 连接成功")
            sys.exit(0)
        else:
            print(f"[✘] SSH 连接失败: {err}")
            sys.exit(1)

    config = _build_config(cli_args, defaults)
    upgrade_type = cli_args.get("--type", "full")

    _print_config(config)

    flow_map = {
        "full": ota_core.run_full_upgrade,
        "mcu": ota_core.run_mcu_upgrade,
        "switch": ota_core.run_switch_upgrade,
        "soc": ota_core.run_soc_upgrade,
    }
    flow_func = flow_map.get(upgrade_type)
    if not flow_func:
        print(f"[错误] 未知升级类型: {upgrade_type}")
        sys.exit(1)

    callbacks = {
        "log": lambda msg, end='\n': print(msg, end=end, flush=True),
        "status": lambda text: print(f"[状态] {text}"),
        "progress": _progress_bar,
    }

    try:
        flow_func(config, callbacks)
    except Exception as e:
        print(f"\n[致命错误] {e}")
        sys.exit(1)
