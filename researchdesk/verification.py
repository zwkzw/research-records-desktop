"""便携运行时验收入口，显式使用全新的目录，不接触正常用户数据。"""

import json
import csv
import sys
import traceback
from datetime import date, datetime
from pathlib import Path

from . import APP_NAME, VERSION
from .reports import export_report
from .exports import export_csv
from .storage import Store, inspect_backup, restore_backup


def verify_release(output: Path) -> int:
    output = output.resolve()
    if output.exists():
        return 2
    output.mkdir(parents=True)
    report = {"app_name": APP_NAME, "version": VERSION,
              "checked_at": datetime.now().astimezone().isoformat(),
              "frozen": bool(getattr(sys, "frozen", False)), "checks": [],
              "visual_inspection": "not_performed"}
    store = None
    app = None
    try:
        from .ui import Application
        store = Store(output / "data")
        project = store.save_project("[验收] 便携版本项目", "只用于打包验收，不是真实实验。")
        app = Application(store, output)
        app.withdraw()
        app.update()
        report["checks"].append("桌面运行时与中文界面初始化")
        app.new_record()
        app.vars["title"].set("[验收] 中文保存与恢复")
        app.vars["note_date"].set(date.today().isoformat())
        app.texts["parameters"].insert("1.0", "温度：25℃（演示）\n次数：3（演示）")
        app.texts["results"].insert("1.0", "程序运行检查，没有进行真实实验。")
        app.update()
        if not app.save_record():
            raise AssertionError("界面保存未成功")
        rid = app.current_record
        if store.record(rid)["title"] != "[验收] 中文保存与恢复":
            raise AssertionError("保存后数据不一致")
        report["checks"].append("通过界面回调保存中文实验记录")
        source = output / "演示附件.txt"
        source.write_text("演示附件，不是真实实验资料。", encoding="utf-8")
        aid = store.add_attachment(rid, source)
        if store.attachment_path(aid).read_bytes() != source.read_bytes():
            raise AssertionError("附件副本不一致")
        report["checks"].append("附件复制与完整性校验")
        if len(store.records(query="中文保存")) != 1:
            raise AssertionError("检索失败")
        export_report(store, rid, output / "验收报告.html")
        report["checks"].append("搜索与HTML报告导出")
        backup = output / "验收备份.zip"
        store.create_backup(backup)
        if inspect_backup(backup)["attachments"] != 1:
            raise AssertionError("备份内容不完整")
        restore_backup(backup, output / "restored")
        restored = Store(output / "restored")
        try:
            if restored.record(rid)["parameters"] != store.record(rid)["parameters"]:
                raise AssertionError("恢复内容不一致")
            if restored.attachment_path(aid).read_bytes() != source.read_bytes():
                raise AssertionError("恢复附件不一致")
        finally:
            restored.close()
        report["checks"].append("完整备份、校验与新目录恢复")
        app.destroy()
        app = None
        store.close()
        store = Store(output / "data")
        if store.record(rid)["title"] != "[验收] 中文保存与恢复":
            raise AssertionError("重开数据库后内容不一致")
        report["checks"].append("关闭数据连接后重新读取")
        app = Application(store, output)
        app.withdraw()
        app.load_record(rid)
        app.repeat_record()
        app.update()
        if app.current_record is not None or app.texts["results"].get("1.0", "end-1c"):
            raise AssertionError("复用实验条件时错误保留旧结果")
        app.vars["title"].set("[验收] 复测草稿")
        app._write_draft()
        app.destroy()
        app = Application(store, output)
        app.withdraw()
        if app.pending_draft is None:
            raise AssertionError("重新打开窗口后未发现草稿")
        app.recover_draft()
        if not app.save_record() or len(store.records()) != 2 or store.read_draft() is not None:
            raise AssertionError("恢复草稿后未能独立保存新记录")
        report["checks"].append("复用实验条件、窗口重开后草稿恢复并正式保存")
        target = export_csv(store.records(query="复测"), output / "筛选汇总.csv")
        with target.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        if len(rows) != 1 or rows[0]["标题"] != "[验收] 复测草稿":
            raise AssertionError("筛选结果CSV不一致")
        report["checks"].append("中文CSV筛选导出与重新读取")
        report["passed"] = True
    except Exception:
        report["passed"] = False
        report["error"] = traceback.format_exc()
    finally:
        if app:
            app.destroy()
        if store:
            store.close()
        (output / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report.get("passed") else 1
