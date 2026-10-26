"""界面与数据层集成测试，使用临时目录及替代对话框。"""

import tempfile
import csv
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from researchdesk.storage import Store
from researchdesk.ui import Application


class InterfaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="research-ui-test-")
        self.store = Store(Path(self.temp.name) / "data")
        self.project = self.store.save_project("界面验证项目")
        self.app = Application(self.store, Path(self.temp.name))
        self.app.withdraw()
        self.app.update()
        self.error_patch = patch("researchdesk.ui.messagebox.showerror", side_effect=lambda *a, **k: (_ for _ in ()).throw(AssertionError(str(a))))
        self.error_patch.start()
        self.prompt_patch = patch("researchdesk.ui.messagebox.askyesnocancel", side_effect=AssertionError("出现未预期的保存对话框"))
        self.prompt_patch.start()

    def tearDown(self):
        self.error_patch.stop()
        self.prompt_patch.stop()
        self.app.destroy()
        self.store.close()
        self.temp.cleanup()

    def edit_title(self, title):
        self.app.vars["title"].set(title)
        self.app.update()

    def test_create_save_reopen_and_edit_widget_content(self):
        self.app.new_record()
        self.edit_title("界面创建的实验记录")
        self.app.texts["parameters"].insert("1.0", "温度：25℃\n次数：3")
        self.app.update()
        self.assertTrue(self.app.dirty)
        self.assertTrue(self.app.save_record())
        rid = self.app.current_record
        self.assertEqual(self.store.record(rid)["parameters"], "温度：25℃\n次数：3")
        self.assertFalse(self.app.dirty)
        self.app._welcome()
        self.app.load_record(rid)
        self.assertEqual(self.app.vars["title"].get(), "界面创建的实验记录")
        self.assertEqual(self.app.texts["parameters"].get("1.0", "end-1c"), "温度：25℃\n次数：3")

    def test_cancel_navigation_preserves_unsaved_changes(self):
        self.app.new_record()
        self.edit_title("还没保存")
        with patch("researchdesk.ui.messagebox.askyesnocancel", return_value=None):
            self.app.choose_project(None)
        self.assertTrue(self.app.record_open)
        self.assertTrue(self.app.dirty)
        self.assertEqual(self.app.vars["title"].get(), "还没保存")
        self.assertEqual(self.store.records(), [])

    def test_search_archive_and_recover(self):
        self.app.new_record()
        self.edit_title("待归档记录")
        self.app.save_record()
        rid = self.app.current_record
        self.app.archive()
        self.assertTrue(self.store.record(rid)["archived"])
        self.app.choose_project(None, True)
        self.app.load_record(rid)
        self.app.archive()
        self.app.choose_project(None)
        self.app.query.set("待归档")
        self.app.search()
        self.assertEqual(self.app.records_tree.get_children(), (str(rid),))

    def test_ui_restore_switches_store_and_persists_new_location(self):
        self.app.new_record()
        self.edit_title("恢复前的记录")
        self.app.save_record()
        backup = Path(self.temp.name) / "backup.zip"
        self.store.create_backup(backup)
        self.app.config_path = Path(self.temp.name) / "config.json"
        with patch("researchdesk.ui.filedialog.askopenfilename", return_value=str(backup)), \
             patch("researchdesk.ui.filedialog.askdirectory", return_value=self.temp.name), \
             patch("researchdesk.ui.messagebox.askyesno", return_value=True), \
             patch("researchdesk.ui.messagebox.showinfo"):
            self.app.restore()
        original_root = self.store.root
        self.store = self.app.store
        self.assertNotEqual(self.store.root, original_root)
        self.assertTrue((original_root / "records.sqlite3").exists())
        self.assertEqual(self.store.records()[0]["title"], "恢复前的记录")
        self.assertTrue(self.app.config_path.is_file())

    def test_timed_draft_survives_abrupt_window_close(self):
        self.app.new_record()
        self.edit_title("尚未正式保存")
        self.app.texts["results"].insert("1.0", "草稿观察\n第二行")
        # 等待真实 Tk 定时器，而不是直接调用保存函数。
        self.app.after(1400, self.app.quit)
        self.app.mainloop()
        self.assertEqual(self.store.records(), [])
        self.assertEqual(self.store.read_draft()["values"]["results"], "草稿观察\n第二行")
        self.app.destroy()
        self.store.close()
        self.store = Store(Path(self.temp.name) / "data")
        self.app = Application(self.store, Path(self.temp.name))
        self.app.withdraw()
        self.app.update()
        self.assertIsNotNone(self.app.pending_draft)
        self.app.recover_draft()
        self.app.update()
        self.assertEqual(self.app.vars["title"].get(), "尚未正式保存")
        self.assertTrue(self.app.dirty)
        self.assertTrue(self.app.save_record())
        self.assertEqual(len(self.store.records()), 1)
        self.assertIsNone(self.store.read_draft())

    def test_existing_record_draft_is_recovered_without_duplicate_record(self):
        self.app.new_record()
        self.edit_title("已有记录")
        self.app.save_record()
        rid = self.app.current_record
        self.edit_title("未正式保存的修改")
        self.app._write_draft()
        self.assertEqual(self.store.record(rid)["title"], "已有记录")
        self.app.pending_draft = self.store.read_draft()
        self.app.recover_draft()
        self.assertEqual(self.app.current_record, rid)
        self.app.save_record()
        self.assertEqual(self.store.record(rid)["title"], "未正式保存的修改")
        self.assertEqual(len(self.store.records()), 1)

    def test_pending_draft_cannot_be_silently_overwritten(self):
        self.store.save_draft({"project_id": self.project, "title": "原草稿"})
        self.app.pending_draft = self.store.read_draft()
        with patch("researchdesk.ui.messagebox.askyesnocancel", return_value=None):
            self.app.new_record()
        self.assertFalse(self.app.record_open)
        self.assertEqual(self.store.read_draft()["values"]["title"], "原草稿")
        with patch("researchdesk.ui.messagebox.askyesnocancel", return_value=True):
            self.app.new_record()
        self.assertEqual(self.app.vars["title"].get(), "原草稿")

    def test_discard_draft_cancels_delayed_writer(self):
        self.app.new_record()
        self.edit_title("主动放弃的草稿")
        self.app._write_draft()
        self.edit_title("放弃前再编辑")
        with patch("researchdesk.ui.messagebox.askyesnocancel", return_value=False):
            self.app.choose_project(None)
        self.assertIsNone(self.app.draft_job)
        self.assertIsNone(self.store.read_draft())
        self.assertFalse(self.app.record_open)

    def test_repeat_conditions_clears_outcomes_and_attachments_and_keeps_original(self):
        self.app.new_record()
        self.edit_title("重复实验")
        self.app.texts["parameters"].insert("1.0", "温度：25℃")
        self.app.texts["results"].insert("1.0", "仅属于第一次的结果")
        self.app.save_record()
        rid = self.app.current_record
        original_file = Path(self.temp.name) / "原始数据.txt"
        original_file.write_text("first", encoding="utf-8")
        self.store.add_attachment(rid, original_file)
        self.app.repeat_record()
        self.app.update()  # 消化选择事件，不能重新打开旧记录覆盖新表单。
        self.assertIsNone(self.app.current_record)
        self.assertEqual(self.app.texts["results"].get("1.0", "end-1c"), "")
        self.assertEqual(self.app.texts["parameters"].get("1.0", "end-1c"), "温度：25℃")
        self.assertEqual(self.app.files_tree.get_children(), ())
        self.assertTrue(self.app.save_record())
        self.assertNotEqual(self.app.current_record, rid)
        self.assertEqual(self.store.record(rid)["results"], "仅属于第一次的结果")
        self.assertEqual(len(self.store.attachments(rid)), 1)

    def test_csv_uses_current_project_keyword_and_date_filters(self):
        self.store.save_record({"project_id": self.project, "title": "导出目标", "note_date": "2026-10-26"})
        self.store.save_record({"project_id": self.project, "title": "范围以外", "note_date": "2026-10-25"})
        other = self.store.save_project("其他项目")
        self.store.save_record({"project_id": other, "title": "导出目标", "note_date": "2026-10-26"})
        self.app.choose_project(self.project)
        self.app.query.set("导出")
        self.app.date_from.set("2026-10-26")
        target = Path(self.temp.name) / "汇总.csv"
        with patch("researchdesk.ui.filedialog.asksaveasfilename", return_value=str(target)), \
             patch("researchdesk.ui.messagebox.showinfo"):
            self.app.export_list()
        with target.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["标题"], "导出目标")

    def test_failed_draft_write_keeps_editor_content(self):
        self.app.new_record()
        self.edit_title("必须保留的输入")
        with patch.object(self.store, "save_draft", side_effect=OSError("磁盘已满")):
            self.app._write_draft()
        self.assertIn("未能保留", self.app.status_text.get())
        self.assertTrue(self.app.dirty)
        self.assertEqual(self.app.vars["title"].get(), "必须保留的输入")


if __name__ == "__main__":
    unittest.main(verbosity=2)
