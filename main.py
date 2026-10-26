"""应用入口。打包后默认将数据保存在程序旁，支持显式指定独立试用目录。"""

import argparse
import json
import sys
from pathlib import Path

from researchdesk.storage import Store


def main():
    parser = argparse.ArgumentParser(description="实验记录与科研文件管理软件 V1.0")
    parser.add_argument("--data-dir", type=Path, help="指定独立数据目录")
    parser.add_argument("--demo", action="store_true", help="创建明确标注为演示的数据")
    parser.add_argument("--verify-release", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.verify_release is not None:
        from researchdesk.verification import verify_release
        return verify_release(args.verify_release)
    home = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    config = home / "本地配置.json"
    data = args.data_dir
    try:
        if data is None:
            if config.exists():
                saved = json.loads(config.read_text(encoding="utf-8"))
                data = Path(saved["data_dir"])
                if not data.is_dir() or not (data / "records.sqlite3").is_file():
                    raise ValueError("配置中的数据目录不存在，请将原目录放回，或使用 --data-dir 指定正确目录。")
            else:
                data = home / "用户数据"
        store = Store(data)
        if args.demo:
            from researchdesk.demo import seed_demo
            seed_demo(store)
        from researchdesk.ui import Application
        app = Application(store, home, config_path=config if args.data_dir is None else None)
        app.mainloop()
    except Exception as exc:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("无法启动", f"软件未能启动：\n{exc}\n\n请确认程序目录和数据目录可以写入。", parent=root)
        root.destroy()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
