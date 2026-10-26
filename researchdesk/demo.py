"""用户主动选择后创建的演示记录，明确区别于真实实验数据。"""

import tempfile
from datetime import date
from pathlib import Path

from .storage import Store


def seed_demo(store: Store) -> int:
    existing = store.conn.execute("SELECT value FROM meta WHERE key='demo_project'").fetchone()
    if existing and store.conn.execute("SELECT id FROM projects WHERE id=?", (existing[0],)).fetchone():
        return int(existing[0])
    project = store.save_project("[演示] 温度传感器测试", "学习记录、附件归档、检索和报告导出。全部内容为演示，不代表真实实验。", "演示,传感器")
    samples = [
        ("[演示] 环境温度下的重复采样", "采样间隔：1秒\n重复次数：3次\n环境：室内", "演示记录：对三次采样进行整理。此处没有真实测量数据。", "下一步：根据自己的设备填写实际参数。", "演示,重复采样"),
        ("[演示] 升温过程的记录模板", "设定温度：40℃（示例）\n记录时长：60秒（示例）", "演示记录：在这里填写读数变化、异常现象和实验照片说明。", "检查传感器位置和记录是否完整。", "演示,升温"),
        ("[演示] 整理记录与导出报告", "检查项：标题、参数、步骤、附件", "这条记录展示如何保存操作说明和待办事项。", "选中此记录，点击导出报告；使用备份功能保存完整演示项目。", "演示,使用指南"),
    ]
    first = None
    for title, parameters, results, issues, tags in samples:
        rid = store.save_record({"project_id": project, "title": title, "note_date": date.today().isoformat(),
            "purpose": "学习软件的基本操作。本记录为演示内容。", "equipment": "示例设备：温度传感器、电脑（未进行实际实验）",
            "parameters": parameters, "steps": "1. 填写实验目的和参数。\n2. 关联原始数据或照片。\n3. 保存后检查，再导出报告。",
            "results": results, "issues": issues, "tags": tags})
        first = first or rid
    with tempfile.TemporaryDirectory(prefix="research-demo-") as temp:
        sample = Path(temp) / "演示说明.txt"
        sample.write_text("这是用于验证附件归档功能的演示文件。\n没有包含真实实验数据。\n", encoding="utf-8")
        store.add_attachment(first, sample)
    with store.conn:
        store.conn.execute("INSERT OR REPLACE INTO meta VALUES('demo_project',?)", (str(project),))
    return project
