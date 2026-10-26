"""从保存的真实记录生成自包含HTML报告，不执行记录中的HTML。"""

from html import escape
from pathlib import Path

from . import APP_NAME, VERSION
from .storage import Store, now


def export_report(store: Store, record_id: int, destination: str | Path) -> Path:
    record = store.record(record_id)
    target = Path(destination).resolve()
    if target.exists():
        raise ValueError("目标报告已存在，请使用新的文件名。")
    target.parent.mkdir(parents=True, exist_ok=True)
    def esc(value):
        return escape(str(value), quote=True)
    sections = "".join(
        f"<section><h2>{label}</h2><div class='content'>{esc(record[key]) or '未填写'}</div></section>"
        for key, label in (("purpose", "实验目的"), ("equipment", "设备与材料"),
                           ("parameters", "实验参数"), ("steps", "实验步骤"),
                           ("results", "结果与观察"), ("issues", "问题与后续计划"))
    )
    attachments = store.attachments(record_id)
    rows = "".join(f"<tr><td>{esc(a['original_name'])}</td><td>{a['size']:,} 字节</td>"
                   f"<td class='hash'>{esc(a['sha256'])}</td></tr>" for a in attachments)
    file_section = ("<table><thead><tr><th>附件名称</th><th>大小</th><th>SHA-256校验值</th>"
                    "</tr></thead><tbody>" + rows + "</tbody></table>" if rows else "<p>暂无附件。</p>")
    html = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(record['title'])} — 实验记录报告</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#f1f5f7;color:#243345;font:15px/1.75 'Microsoft YaHei',sans-serif}}
main{{max-width:960px;margin:36px auto;background:white;padding:48px 56px;border-radius:12px}}
.eyebrow{{font-size:12px;color:#367a78;letter-spacing:2px}}h1{{font-size:29px;line-height:1.4;margin:12px 0 22px;overflow-wrap:anywhere}}
.meta{{color:#596c7d;background:#f3f7f8;padding:18px;border-radius:8px;overflow-wrap:anywhere}}
h2{{font-size:17px;margin:30px 0 10px;border-left:3px solid #248681;padding-left:12px}}
.content{{white-space:pre-wrap;overflow-wrap:anywhere}}table{{border-collapse:collapse;width:100%;table-layout:fixed;font-size:12px}}
th,td{{padding:10px;text-align:left;border:1px solid #dce4e8;overflow-wrap:anywhere}}th{{background:#f3f7f8}}
th:first-child{{width:32%}}th:nth-child(2){{width:17%}}.hash{{font-family:monospace}}footer{{border-top:1px solid #e1e8eb;margin-top:36px;padding-top:16px;font-size:11px;color:#758594}}
@media print{{body{{background:white}}main{{margin:0;padding:12mm;max-width:none}}h2{{break-after:avoid}}tr{{break-inside:avoid}}}}
</style></head><body><main><div class="eyebrow">实验记录报告 · {esc(VERSION)}</div>
<h1>{esc(record['title'])}</h1><div class="meta">所属项目：{esc(record['project_name'])}<br>
实验日期：{esc(record['note_date'])}<br>标签：{esc(record['tags']) or '无'}<br>
保存时间：{esc(record['updated_at'])}</div>{sections}<section><h2>附件清单（{len(attachments)}）</h2>
{file_section}<p style="font-size:12px;color:#758594">本报告列出附件信息，不包含附件文件。迁移完整数据请使用软件的备份功能。</p></section>
<footer>由{esc(APP_NAME)} {esc(VERSION)}导出 · {esc(now())}<br>内容来自用户保存的记录，软件不自动验证实验结论。</footer>
</main></body></html>"""
    with target.open("x", encoding="utf-8") as stream:
        stream.write(html)
    return target
