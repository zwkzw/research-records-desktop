"""筛选记录导出为中文 CSV；不把用户输入作为表格公式执行。"""

import csv
from pathlib import Path


COLUMNS = (
    ("id", "记录编号"), ("project_name", "所属项目"), ("title", "标题"),
    ("note_date", "实验日期"), ("tags", "标签"), ("purpose", "实验目的"),
    ("equipment", "设备与材料"), ("parameters", "实验参数"), ("steps", "实验步骤"),
    ("results", "结果与观察"), ("issues", "问题与后续计划"),
    ("attachment_count", "附件数量"), ("archived", "已归档"),
    ("created_at", "创建时间"), ("updated_at", "保存时间"),
)


def spreadsheet_text(value):
    text = str(value)
    # CSV 引号不能阻止公式执行；危险开头（含前置空白）按文本交付。
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


def export_csv(records: list[dict], destination: str | Path) -> Path:
    if not records:
        raise ValueError("当前筛选没有记录，请调整条件后再导出。")
    target = Path(destination).resolve()
    if target.exists():
        raise ValueError("目标文件已存在，请选择新的导出文件名。")
    target.parent.mkdir(parents=True, exist_ok=True)
    # UTF-8 BOM 便于 Excel 直接识别中文；csv 模块处理换行、逗号和引号。
    with target.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow([label for _, label in COLUMNS])
        for record in records:
            writer.writerow([spreadsheet_text(record.get(key, "")) for key, _ in COLUMNS])
    return target
