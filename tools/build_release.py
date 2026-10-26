"""构建Windows便携包。每次使用新的输出目录，避免覆盖用户数据。"""

import argparse
import json
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=f"releases/preview-{datetime.now():%Y%m%d-%H%M%S}")
    args = parser.parse_args()
    output = (ROOT / args.output).resolve()
    if not output.is_relative_to(ROOT) or output.exists():
        raise SystemExit("请选择项目内尚不存在的发布目录，避免覆盖现有文件。")
    sys.path.insert(0, str(ROOT / ".build-tools"))
    import PyInstaller.__main__
    output.parent.mkdir(parents=True, exist_ok=True)
    PyInstaller.__main__.run([
        str(ROOT / "main.py"), "--onedir", "--windowed", "--name", "ResearchRecords",
        "--distpath", str(output), "--workpath", str(ROOT / "build" / output.name),
        "--specpath", str(ROOT / "build"), "--noupx", "--log-level", "WARN",
        "--exclude-module", "PIL", "--exclude-module", "numpy", "--exclude-module", "matplotlib",
    ])
    app = output / "ResearchRecords"
    for name in ("使用说明.md", "第三方组件说明.md", "LICENSE"):
        shutil.copy2(ROOT / name, app / name)
    licenses = app / "licenses"
    licenses.mkdir()
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        shutil.copy2(python_license, licenses / "Python-LICENSE.txt")
    for component in ("tcl8.6", "tk8.6"):
        source = Path(sys.base_prefix) / "tcl" / component / "license.terms"
        if source.is_file():
            shutil.copy2(source, licenses / f"{component}-license.terms")
    # Python的Windows分发有时将Tcl许可放在tcl8目录而非tcl8.6目录。
    if not (licenses / "tcl8.6-license.terms").exists():
        candidates = list((Path(sys.base_prefix) / "tcl").glob("**/license.terms"))
        for source in candidates:
            if "tk8.6" not in str(source):
                shutil.copy2(source, licenses / "Tcl-license.terms")
                break
    archive = output / "实验记录管理_V1.0_便携试用版.zip"
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as bundle:
        for file in sorted(app.rglob("*")):
            if file.is_file():
                bundle.write(file, Path("实验记录管理试用版") / file.relative_to(app))
    info = {"built_at": datetime.now().astimezone().isoformat(), "platform": sys.platform,
            "python": sys.version, "pyinstaller": __import__("PyInstaller").__version__,
            "executable": str(app / "ResearchRecords.exe"), "archive": str(archive),
            "archive_bytes": archive.stat().st_size}
    (output / "build-info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(info, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
