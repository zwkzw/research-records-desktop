"""临时目录中的行为测试，不读取或修改用户的真实实验资料。"""

import hashlib
import csv
import json
import sqlite3
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

from researchdesk import APP_ID
from researchdesk.demo import seed_demo
from researchdesk.exports import export_csv
from researchdesk.reports import export_report
from researchdesk.storage import DB_NAME, Store, digest, inspect_backup, restore_backup


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="research-test-")
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "中文数据目录")
        self.project = self.store.save_project("光学测试", "测试目标", "光学,研发")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def record(self, **updates):
        values = {"project_id": self.project, "title": "实验记录", "note_date": "2026-10-26",
                  "purpose": "检查稳定性", "parameters": "频率：100Hz\n重复：3次", "tags": "光学,稳定性"}
        values.update(updates)
        return self.store.save_record(values)

    def attach(self, rid, content=b"original", filename="原始数据.csv"):
        source = self.root / filename
        source.write_bytes(content)
        aid = self.store.add_attachment(rid, source)
        return aid, source

    def backup(self):
        target = self.root / "完整备份.zip"
        self.store.create_backup(target)
        return target

    def rewrite_zip(self, source, replacements=None, extras=None, name="修改后的备份.zip"):
        target = self.root / name
        with zipfile.ZipFile(source) as src, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as dst:
            for entry in src.infolist():
                dst.writestr(entry, (replacements or {}).get(entry.filename, src.read(entry.filename)))
            for filename, value in (extras or {}).items():
                dst.writestr(filename, value)
        return target

    def test_create_update_and_reopen(self):
        rid = self.record(results="中文结果\n第二行")
        values = self.store.record(rid)
        values.update(title="修改后的标题", equipment="传感器A")
        self.store.save_record(values, rid)
        self.store.save_project("新项目名", "新目标", project_id=self.project, status="已完成")
        self.store.close()
        self.store = Store(self.root / "中文数据目录")
        reopened = self.store.record(rid)
        self.assertEqual(reopened["results"], "中文结果\n第二行")
        self.assertEqual(reopened["title"], "修改后的标题")
        self.assertEqual(reopened["project_name"], "新项目名")
        self.assertEqual(self.store.projects()[0]["record_count"], 1)

    def test_validation_and_literal_search(self):
        rid = self.record(title="100%_成功", results="独特正文")
        self.record(title="另一条", note_date="2026-10-25")
        for term in ("100%_", "独特正文", "光学"):
            self.assertIn(rid, [r["id"] for r in self.store.records(query=term)])
        self.assertEqual(len(self.store.records(query="%_")), 1)
        self.assertEqual(len(self.store.records(date_from="2026-10-26", date_to="2026-10-26")), 1)
        with self.assertRaises(ValueError):
            self.store.records(date_from="2026-02-30")
        with self.assertRaises(ValueError):
            self.store.records(date_from="2026-10-27", date_to="2026-10-25")
        with self.assertRaises(ValueError):
            self.record(project_id=9999)
        with self.assertRaises(ValueError):
            self.record(title="  ")
        with self.assertRaises(ValueError):
            self.record(note_date="2026-1-1")

    def test_archive_does_not_delete_files(self):
        rid = self.record()
        aid, original = self.attach(rid)
        managed = self.store.attachment_path(aid)
        self.store.archive_record(rid)
        self.assertEqual(self.store.records(), [])
        self.assertEqual(self.store.records(archived=True)[0]["id"], rid)
        self.assertTrue(original.exists())
        self.assertTrue(managed.exists())
        self.store.archive_record(rid, False)
        self.assertEqual(len(self.store.records()), 1)

    def test_same_named_attachments_are_distinct_and_original_preserved(self):
        rid = self.record()
        aid1, original = self.attach(rid, b"version1")
        aid2, _ = self.attach(rid, b"version2")
        self.assertEqual(original.read_bytes(), b"version2")
        self.assertEqual(self.store.attachment_path(aid1).read_bytes(), b"version1")
        self.assertEqual(self.store.attachment_path(aid2).read_bytes(), b"version2")
        self.assertNotEqual(self.store.attachment_path(aid1), self.store.attachment_path(aid2))

    def test_tampered_attachment_fails_open_and_backup(self):
        rid = self.record()
        aid, original = self.attach(rid)
        self.store.attachment_path(aid).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "归档时不同"):
            self.store.attachment_path(aid)
        with self.assertRaises(ValueError):
            self.backup()
        self.assertEqual(original.read_bytes(), b"original")

    def test_report_escapes_content_and_does_not_overwrite(self):
        rid = self.record(title='<script>alert("x")</script>', results="A&B\n<观察>")
        self.attach(rid, b"test", "特殊&名字.txt")
        target = export_report(self.store, rid, self.root / "中文报告.html")
        html = target.read_text(encoding="utf-8")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("A&amp;B", html)
        self.assertIn("特殊&amp;名字.txt", html)
        self.assertIn("频率：100Hz", html)
        with self.assertRaises(ValueError):
            export_report(self.store, rid, target)

    def test_backup_restore_roundtrip_including_archived_and_empty_projects(self):
        rid = self.record()
        aid, _ = self.attach(rid, b"all-data")
        self.store.archive_record(rid)
        self.store.save_project("空项目")
        backup = self.backup()
        info = inspect_backup(backup)
        self.assertEqual((info["projects"], info["records"], info["attachments"]), (2, 1, 1))
        destination = self.root / "全新恢复目录"
        restore_backup(backup, destination)
        restored = Store(destination)
        try:
            self.assertEqual(restored.record(rid)["archived"], 1)
            self.assertEqual(restored.attachment_path(aid).read_bytes(), b"all-data")
            self.assertEqual(restored.record(rid)["parameters"], "频率：100Hz\n重复：3次")
            self.assertEqual(len(restored.projects()), 2)
        finally:
            restored.close()
        self.assertTrue((self.store.root / DB_NAME).exists())

    def test_restore_never_overwrites_existing_directory(self):
        self.record()
        backup = self.backup()
        folder = self.root / "现有数据"
        folder.mkdir()
        marker = folder / "必须保留.txt"
        marker.write_text("keep", encoding="utf-8")
        with self.assertRaises(ValueError):
            restore_backup(backup, folder)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_rejects_traversal_and_does_not_leave_destination(self):
        self.record()
        backup = self.backup()
        for index, path in enumerate(("../outside.txt", "/absolute.txt", "attachments/../../bad.txt", "C:\\bad.txt")):
            malicious = self.rewrite_zip(backup, extras={path: b"bad"}, name=f"bad{index}.zip")
            destination = self.root / f"no-output-{index}"
            with self.assertRaises(ValueError):
                restore_backup(malicious, destination)
            self.assertFalse(destination.exists())
        self.assertFalse((self.root / "outside.txt").exists())

    def test_corrupt_payload_is_rejected(self):
        self.record()
        backup = self.backup()
        modified = self.rewrite_zip(backup, replacements={DB_NAME: b"not a database"})
        with self.assertRaises(ValueError):
            inspect_backup(modified)

    def test_untrusted_database_trigger_rejected_even_with_matching_hash(self):
        self.record()
        backup = self.backup()
        payload = self.root / "modified.sqlite3"
        with zipfile.ZipFile(backup) as archive:
            payload.write_bytes(archive.read(DB_NAME))
            manifest = json.loads(archive.read("manifest.json"))
        with sqlite3.connect(payload) as conn:
            conn.execute("CREATE TRIGGER unsafe AFTER INSERT ON projects BEGIN DELETE FROM records; END")
        conn.close()
        data = payload.read_bytes()
        item = next(f for f in manifest["files"] if f["path"] == DB_NAME)
        item.update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
        modified = self.rewrite_zip(backup, replacements={DB_NAME: data, "manifest.json": json.dumps(manifest).encode()})
        with self.assertRaisesRegex(ValueError, "不支持的数据库对象"):
            inspect_backup(modified)

    def test_rejects_duplicate_names_and_symlinks(self):
        self.record()
        backup = self.backup()
        duplicate = self.rewrite_zip(backup, extras={"manifest.json": b"{}"})
        with self.assertRaises(ValueError):
            inspect_backup(duplicate)
        symlink = self.root / "symlink.zip"
        with zipfile.ZipFile(backup) as original, zipfile.ZipFile(symlink, "w") as out:
            for entry in original.infolist():
                if entry.filename == DB_NAME:
                    entry.create_system = 3
                    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                out.writestr(entry, original.read(entry.filename))
        with self.assertRaises(ValueError):
            inspect_backup(symlink)

    def test_demo_is_explicit_and_idempotent(self):
        first = seed_demo(self.store)
        second = seed_demo(self.store)
        self.assertEqual(first, second)
        records = self.store.records(first)
        self.assertEqual(len(records), 3)
        self.assertTrue(all(r["title"].startswith("[演示]") for r in records))

    def test_draft_backup_roundtrip_and_clear(self):
        values = {"project_id": self.project, "title": "", "note_date": "未完成日期", "results": "未确认结果"}
        self.store.save_draft(values)
        self.assertEqual(self.store.records(), [])
        backup = self.backup()
        restore_backup(backup, self.root / "恢复草稿")
        restored = Store(self.root / "恢复草稿")
        try:
            self.assertEqual(restored.read_draft()["values"]["results"], "未确认结果")
            restored.clear_draft()
            self.assertIsNone(restored.read_draft())
            self.assertIsNotNone(self.store.read_draft())
        finally:
            restored.close()

    def test_csv_preserves_chinese_quotes_newlines_and_protects_formula_cells(self):
        rid = self.record(title='=HYPERLINK("example")', results='中文,换行\n"观察"', parameters="  +1+2", issues="\t危险")
        target = export_csv(self.store.records(), self.root / "筛选导出.csv")
        self.assertTrue(target.read_bytes().startswith(b"\xef\xbb\xbf"))
        with target.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertTrue(rows[0]["标题"].startswith("'="))
        self.assertTrue(rows[0]["实验参数"].startswith("'+"))
        self.assertEqual(rows[0]["结果与观察"], '中文,换行\n"观察"')
        self.assertTrue(self.store.record(rid)["title"].startswith("="))
        with self.assertRaises(ValueError):
            export_csv(self.store.records(), target)
        with self.assertRaises(ValueError):
            export_csv([], self.root / "空.csv")
        self.assertFalse((self.root / "空.csv").exists())

    def test_corrupt_draft_is_reported_without_discarding_data(self):
        self.record()
        with self.store.conn:
            self.store.conn.execute("INSERT INTO meta VALUES('editor_draft','[]')")
        with self.assertRaises(ValueError):
            self.store.read_draft()
        self.assertEqual(len(self.store.records()), 1)
        self.assertIsNotNone(self.store.conn.execute("SELECT value FROM meta WHERE key='editor_draft'").fetchone())


if __name__ == "__main__":
    unittest.main(verbosity=2)
