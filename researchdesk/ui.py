"""中文桌面界面。界面操作与数据层分离，便于验证保存和备份行为。"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import tkinter as tk
import webbrowser
from datetime import date, datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import APP_NAME, VERSION
from .demo import seed_demo
from .exports import export_csv
from .reports import export_report
from .storage import RECORD_FIELDS, Store, inspect_backup, restore_backup


BG = "#f1f5f7"
NAVY = "#182c3b"
TEAL = "#167d79"
INK = "#253b4d"
MUTED = "#697f8e"
FONT = "Microsoft YaHei UI"


def human_size(size):
    return f"{size / 1024 / 1024:.1f} MB" if size >= 1024 * 1024 else f"{max(1, round(size / 1024))} KB"


class ProjectDialog(tk.Toplevel):
    def __init__(self, parent, project=None):
        super().__init__(parent)
        self.result = None
        self.title("项目设置" if project else "新建项目")
        self.transient(parent)
        self.resizable(False, False)
        self.configure(bg="white")
        frame = ttk.Frame(self, style="White.TFrame", padding=24)
        frame.pack(fill="both", expand=True)
        self.fields = {}
        for i, (key, label) in enumerate((("name", "项目名称 *"), ("tags", "项目标签（用逗号分隔）"), ("goal", "研究目标或说明"))):
            ttk.Label(frame, text=label, style="White.TLabel").grid(row=i * 2, column=0, sticky="w", pady=(10, 6))
            if key == "goal":
                widget = tk.Text(frame, width=48, height=5, font=(FONT, 10), relief="solid", bd=1, wrap="word")
                widget.insert("1.0", (project or {}).get(key, ""))
            else:
                widget = ttk.Entry(frame, width=48)
                widget.insert(0, (project or {}).get(key, ""))
            widget.grid(row=i * 2 + 1, column=0, sticky="ew")
            self.fields[key] = widget
        ttk.Label(frame, text="项目状态", style="White.TLabel").grid(row=6, column=0, sticky="w", pady=(12, 6))
        self.status = ttk.Combobox(frame, state="readonly", values=("进行中", "已完成", "暂停"))
        self.status.set((project or {}).get("status", "进行中"))
        self.status.grid(row=7, column=0, sticky="ew")
        buttons = ttk.Frame(frame, style="White.TFrame")
        buttons.grid(row=8, column=0, sticky="e", pady=(24, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="left", padx=6)
        ttk.Button(buttons, text="保存项目", style="Primary.TButton", command=self.submit).pack(side="left")
        self.bind("<Escape>", lambda e: self.destroy())
        self.update_idletasks()
        self.geometry(f"+{parent.winfo_rootx() + 140}+{parent.winfo_rooty() + 80}")
        self.fields["name"].focus_set()
        self.grab_set()
        self.wait_window()

    def submit(self):
        result = {k: (w.get("1.0", "end-1c") if isinstance(w, tk.Text) else w.get()).strip()
                  for k, w in self.fields.items()}
        if not result["name"]:
            messagebox.showinfo("还需要项目名称", "填写一个项目名称后再保存。", parent=self)
            return
        result["status"] = self.status.get()
        self.result = result
        self.destroy()


class Application(tk.Tk):
    def __init__(self, store: Store, app_home: Path, config_path: Path | None = None):
        super().__init__()
        self.store, self.app_home, self.config_path = store, Path(app_home), config_path
        self.title(f"{APP_NAME} {VERSION}")
        self.configure(bg=BG)
        width, height = min(1440, self.winfo_screenwidth() - 70), min(920, self.winfo_screenheight() - 100)
        self.geometry(f"{width}x{height}+30+30")
        self.minsize(min(1060, width), min(620, height))
        self.current_project = None
        self.archived_view = False
        self.current_record = None
        self.dirty = False
        self.loading = False
        self.record_open = False
        self.draft_job = None
        self.pending_draft = self.store.read_draft()
        self.project_ids = []
        self.project_options = {}
        self.vars = {k: tk.StringVar() for k in ("title", "note_date", "tags", "project")}
        self.query = tk.StringVar()
        self.date_from, self.date_to = tk.StringVar(), tk.StringVar()
        self.status_text = tk.StringVar(value="就绪 · 数据仅保存在本机")
        self._styles()
        self._layout()
        for variable in self.vars.values():
            variable.trace_add("write", self._mark_dirty)
        self.bind("<Control-s>", lambda e: self.save_record())
        self.bind("<Control-n>", lambda e: self.new_record())
        self.bind("<Control-f>", lambda e: self.search_entry.focus_set())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh_projects()
        self.refresh_records()
        self._welcome()
        if self.pending_draft:
            self.status_text.set("发现上次未保存的草稿 · 点击“恢复草稿”继续编辑")
            self.draft_button.configure(state="normal")

    def _styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        self.option_add("*Font", (FONT, 10))
        style.configure("TFrame", background=BG)
        style.configure("White.TFrame", background="white")
        style.configure("TLabel", background=BG, foreground=INK, font=(FONT, 10))
        style.configure("White.TLabel", background="white", foreground=INK)
        style.configure("Muted.TLabel", foreground=MUTED, font=(FONT, 9))
        style.configure("Header.TLabel", font=(FONT, 22, "bold"), foreground=INK)
        style.configure("TButton", font=(FONT, 10), padding=(13, 8), background="#e7eef1", foreground=INK, borderwidth=0)
        style.map("TButton", background=[("active", "#dbe7eb")])
        style.configure("Primary.TButton", background=TEAL, foreground="white", font=(FONT, 10, "bold"))
        style.map("Primary.TButton", background=[("active", "#126762"), ("disabled", "#91b7b4")])
        style.configure("TEntry", padding=7, fieldbackground="white", bordercolor="#d8e2e8", lightcolor="#d8e2e8")
        style.configure("TCombobox", padding=6, fieldbackground="white", arrowsize=13)
        style.map("TCombobox", fieldbackground=[("readonly", "white")])
        style.configure("Treeview", font=(FONT, 10), rowheight=36, borderwidth=0, background="white", fieldbackground="white", foreground=INK)
        style.map("Treeview", background=[("selected", "#dcefee")], foreground=[("selected", "#125f5b")])
        style.configure("Treeview.Heading", font=(FONT, 9, "bold"), background="#edf3f6", foreground=MUTED, padding=9, borderwidth=0)
        style.configure("TNotebook", background="white", borderwidth=0)
        style.configure("TNotebook.Tab", font=(FONT, 10), padding=(19, 10), background="#edf3f6", foreground=MUTED)
        style.map("TNotebook.Tab", background=[("selected", "white")], foreground=[("selected", TEAL)])

    def _nav_button(self, parent, text, command):
        button = tk.Button(parent, text=text, command=command, bg="#233e50", fg="#e7f0f5",
                           activebackground="#315366", activeforeground="white", relief="flat", bd=0,
                           anchor="w", padx=16, pady=11, cursor="hand2", font=(FONT, 10))
        button.pack(fill="x", pady=3)
        return button

    def _layout(self):
        sidebar = tk.Frame(self, bg=NAVY, width=218)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        tk.Label(sidebar, text="RESEARCH / LOCAL", bg=NAVY, fg="#85b6b6", font=(FONT, 9)).pack(anchor="w", padx=22, pady=(28, 8))
        tk.Label(sidebar, text="研究工作台", bg=NAVY, fg="white", font=(FONT, 20, "bold")).pack(anchor="w", padx=20)
        tk.Label(sidebar, text="让每次实验都有据可查", bg=NAVY, fg="#a3b9c5", font=(FONT, 9)).pack(anchor="w", padx=22, pady=(9, 24))
        nav = tk.Frame(sidebar, bg=NAVY)
        nav.pack(fill="x", padx=12)
        self.all_button = self._nav_button(nav, "全部实验记录", lambda: self.choose_project(None, False))
        self._nav_button(nav, "已归档记录", lambda: self.choose_project(None, True))
        tk.Label(sidebar, text="我的项目", bg=NAVY, fg="#85a0b2", font=(FONT, 9)).pack(anchor="w", padx=22, pady=(25, 10))
        self.project_list = tk.Listbox(sidebar, bg=NAVY, fg="#d5e4ed", selectbackground="#2c5665", selectforeground="white",
                                      highlightthickness=0, bd=0, activestyle="none", font=(FONT, 10), exportselection=False)
        self.project_list.pack(fill="both", expand=True, padx=15)
        self.project_list.bind("<<ListboxSelect>>", self._select_project)
        actions = tk.Frame(sidebar, bg=NAVY)
        actions.pack(fill="x", padx=12, pady=12)
        self._nav_button(actions, "+  新建项目", self.new_project)
        self._nav_button(actions, "项目设置", self.edit_project)
        self.stats_label = tk.Label(sidebar, text="", bg=NAVY, fg="#a4bbc7", font=(FONT, 9), anchor="w", justify="left")
        self.stats_label.pack(fill="x", padx=22, pady=(10, 22))

        main = ttk.Frame(self, padding=(25, 22, 25, 10))
        main.pack(side="left", fill="both", expand=True)
        header = ttk.Frame(main)
        header.pack(fill="x", pady=(0, 12))
        caption = ttk.Frame(header)
        caption.pack(side="left")
        ttk.Label(caption, text="实验记录", style="Header.TLabel").pack(anchor="w")
        ttk.Label(caption, text="记录过程，连接文件，积累可追溯的研究资料。", style="Muted.TLabel").pack(anchor="w", pady=(6, 0))
        utilities = ttk.Frame(main)
        utilities.pack(fill="x", pady=(0, 12))
        for label, command in (("使用说明", self.help), ("导入演示", self.demo), ("备份", self.backup), ("恢复", self.restore)):
            ttk.Button(utilities, text=label, command=command).pack(side="left", padx=(0, 6))
        self.draft_button = ttk.Button(utilities, text="恢复草稿", command=self.recover_draft, state="disabled")
        self.draft_button.pack(side="left", padx=6)

        panes = ttk.Panedwindow(main, orient="horizontal")
        panes.pack(fill="both", expand=True)
        listing = ttk.Frame(panes, style="White.TFrame", padding=16, width=345)
        panes.add(listing, weight=0)
        self.scope_label = ttk.Label(listing, text="全部实验记录", style="White.TLabel", font=(FONT, 12, "bold"))
        self.scope_label.pack(anchor="w", pady=(1, 12))
        search = ttk.Frame(listing, style="White.TFrame")
        search.pack(fill="x")
        self.search_entry = ttk.Entry(search, textvariable=self.query)
        self.search_entry.pack(side="left", fill="x", expand=True)
        self.search_entry.bind("<Return>", lambda e: self.search())
        ttk.Button(search, text="查找", command=self.search).pack(side="left", padx=(5, 0))
        ttk.Label(listing, text="搜索标题、正文、标签或项目", style="White.TLabel", foreground=MUTED, font=(FONT, 9)).pack(anchor="w", pady=(6, 12))
        dates = ttk.Frame(listing, style="White.TFrame")
        dates.pack(fill="x")
        ttk.Entry(dates, textvariable=self.date_from, width=11).pack(side="left", fill="x", expand=True)
        ttk.Label(dates, text=" 至 ", style="White.TLabel").pack(side="left")
        ttk.Entry(dates, textvariable=self.date_to, width=11).pack(side="left", fill="x", expand=True)
        row = ttk.Frame(listing, style="White.TFrame")
        row.pack(fill="x", pady=(8, 14))
        self.count_label = ttk.Label(row, text="0条记录", style="White.TLabel", foreground=MUTED)
        self.count_label.pack(side="left")
        ttk.Button(row, text="清除筛选", command=self.clear_search).pack(side="right")
        treeframe = ttk.Frame(listing, style="White.TFrame")
        treeframe.pack(fill="both", expand=True)
        self.records_tree = ttk.Treeview(treeframe, columns=("title", "date"), show="headings", selectmode="browse")
        self.records_tree.heading("title", text="记录标题")
        self.records_tree.heading("date", text="日期")
        self.records_tree.column("title", width=195, minwidth=110)
        self.records_tree.column("date", width=95, minwidth=85, stretch=False)
        scrollbar = ttk.Scrollbar(treeframe, orient="vertical", command=self.records_tree.yview)
        self.records_tree.configure(yscrollcommand=scrollbar.set)
        self.records_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.records_tree.bind("<<TreeviewSelect>>", self._select_record)
        ttk.Button(listing, text="+  新建实验记录", style="Primary.TButton", command=self.new_record).pack(fill="x", pady=(16, 0))
        ttk.Button(listing, text="导出筛选结果（CSV）", command=self.export_list).pack(fill="x", pady=(7, 0))

        self.detail = ttk.Frame(panes, style="White.TFrame", padding=22)
        panes.add(self.detail, weight=1)
        self.empty = ttk.Frame(self.detail, style="White.TFrame")
        self.empty.place(relx=.5, rely=.43, anchor="center")
        ttk.Label(self.empty, text="从一条实验记录开始", style="White.TLabel", font=(FONT, 20, "bold")).pack(pady=(0, 13))
        ttk.Label(self.empty, text="先建立项目，再把实验参数、观察结果和文件放在一起。\n\n所有内容保存在本机；你也可以先导入演示熟悉操作。",
                  style="White.TLabel", foreground=MUTED, justify="center", wraplength=420).pack(pady=(0, 24))
        ttk.Button(self.empty, text="新建实验记录", style="Primary.TButton", command=self.new_record).pack()
        self.editor = ttk.Frame(self.detail, style="White.TFrame")
        titlebar = ttk.Frame(self.editor, style="White.TFrame")
        titlebar.pack(fill="x")
        self.editor_label = ttk.Label(titlebar, text="记录详情", style="White.TLabel", font=(FONT, 12, "bold"))
        self.editor_label.pack(side="left")
        toolbar = ttk.Frame(self.editor, style="White.TFrame")
        toolbar.pack(fill="x", pady=(10, 0))
        self.archive_button = ttk.Button(toolbar, text="归档", command=self.archive)
        self.archive_button.pack(side="right", padx=(5, 0))
        ttk.Button(toolbar, text="导出报告", command=self.export).pack(side="right", padx=(5, 0))
        self.save_button = ttk.Button(toolbar, text="保存记录", style="Primary.TButton", command=self.save_record)
        self.save_button.pack(side="left")
        ttk.Button(titlebar, text="复用实验条件", command=self.repeat_record).pack(side="right")
        ttk.Label(self.editor, text="标题 *", style="White.TLabel", foreground=MUTED).pack(anchor="w", pady=(18, 6))
        self.title_entry = ttk.Entry(self.editor, textvariable=self.vars["title"], font=(FONT, 14))
        self.title_entry.pack(fill="x")
        meta = ttk.Frame(self.editor, style="White.TFrame")
        meta.pack(fill="x", pady=(12, 10))
        meta.columnconfigure(0, weight=1)
        meta.columnconfigure(1, weight=0)
        ttk.Label(meta, text="所属项目 *", style="White.TLabel", foreground=MUTED).grid(row=0, column=0, sticky="w", pady=(0, 5))
        ttk.Label(meta, text="实验日期 *", style="White.TLabel", foreground=MUTED).grid(row=0, column=1, sticky="w", padx=(10, 0), pady=(0, 5))
        self.project_combo = ttk.Combobox(meta, textvariable=self.vars["project"], state="readonly")
        self.project_combo.grid(row=1, column=0, sticky="ew")
        ttk.Entry(meta, textvariable=self.vars["note_date"], width=13).grid(row=1, column=1, sticky="ew", padx=(10, 0))
        ttk.Label(meta, text="标签（用逗号分隔）", style="White.TLabel", foreground=MUTED).grid(row=2, column=0, columnspan=2, sticky="w", pady=(9, 5))
        ttk.Entry(meta, textvariable=self.vars["tags"]).grid(row=3, column=0, columnspan=2, sticky="ew")
        self.notebook = ttk.Notebook(self.editor)
        self.notebook.pack(fill="both", expand=True, pady=(8, 0))
        contents, outcomes, files = [ttk.Frame(self.notebook, style="White.TFrame", padding=(6, 12, 6, 0)) for _ in range(3)]
        self.notebook.add(contents, text="实验内容")
        self.notebook.add(outcomes, text="结果与计划")
        self.notebook.add(files, text="附件")
        self.texts = {}
        for page, definitions in (
            (contents, (("purpose", "实验目的", 2), ("equipment", "设备与材料", 2), ("parameters", "实验参数（每行一项，可自由填写）", 3), ("steps", "实验步骤", 3))),
            (outcomes, (("results", "结果与观察", 7), ("issues", "问题与后续计划", 5))),
        ):
            # 页面外层可滚动，小屏幕下也能访问底部字段；每个长文本仍单独滚动。
            canvas = tk.Canvas(page, bg="white", highlightthickness=0, width=100, height=100)
            page_scroll = ttk.Scrollbar(page, orient="vertical", command=canvas.yview)
            canvas.configure(yscrollcommand=page_scroll.set)
            page_scroll.pack(side="right", fill="y")
            canvas.pack(side="left", fill="both", expand=True)
            parent = ttk.Frame(canvas, style="White.TFrame")
            window = canvas.create_window((0, 0), window=parent, anchor="nw")
            parent.bind("<Configure>", lambda e, c=canvas: c.configure(scrollregion=c.bbox("all")))
            canvas.bind("<Configure>", lambda e, c=canvas, w=window: c.itemconfigure(w, width=e.width))
            parent.columnconfigure(0, weight=1)
            for index, (key, label, height) in enumerate(definitions):
                parent.rowconfigure(index * 2 + 1, weight=1)
                ttk.Label(parent, text=label, style="White.TLabel", foreground=MUTED).grid(row=index * 2, column=0, sticky="w", pady=(0 if index == 0 else 8, 5))
                holder = ttk.Frame(parent, style="White.TFrame")
                holder.grid(row=index * 2 + 1, column=0, sticky="nsew")
                text = tk.Text(holder, height=height, width=30, font=(FONT, 10), wrap="word", relief="flat", bd=0,
                               highlightthickness=1, highlightbackground="#d9e3e8", highlightcolor=TEAL,
                               padx=10, pady=6, undo=True, foreground=INK, background="#fbfdfe")
                scroll = ttk.Scrollbar(holder, command=text.yview)
                text.configure(yscrollcommand=scroll.set)
                text.pack(side="left", fill="both", expand=True)
                scroll.pack(side="right", fill="y")
                text.bind("<<Modified>>", self._modified)
                self.texts[key] = text
        ttk.Label(files, text="附件会复制到本地归档目录，原始文件保留。单个文件最大100MB。", style="White.TLabel",
                  foreground=MUTED, wraplength=470).pack(anchor="w", pady=(0, 12))
        filetools = ttk.Frame(files, style="White.TFrame")
        filetools.pack(fill="x", pady=(0, 12))
        ttk.Button(filetools, text="+ 添加附件", command=self.add_files).pack(side="left")
        ttk.Button(filetools, text="打开选中附件", command=self.open_attachment).pack(side="left", padx=8)
        self.files_tree = ttk.Treeview(files, columns=("name", "size"), show="headings", selectmode="browse")
        self.files_tree.heading("name", text="原始文件名")
        self.files_tree.heading("size", text="大小")
        self.files_tree.column("name", width=300)
        self.files_tree.column("size", width=80, stretch=False)
        self.files_tree.pack(fill="both", expand=True)
        self.files_tree.bind("<Double-1>", lambda e: self.open_attachment())
        self.saved_label = ttk.Label(self.editor, text="", style="White.TLabel", foreground=MUTED, font=(FONT, 9))
        self.saved_label.pack(anchor="w", pady=(12, 0))
        footer = ttk.Frame(main)
        footer.pack(fill="x", pady=(12, 0))
        ttk.Label(footer, textvariable=self.status_text, style="Muted.TLabel").pack(side="left")
        ttk.Label(footer, text=f"本地版 · {VERSION}", style="Muted.TLabel").pack(side="right")

    def report_error(self, exc):
        self.status_text.set("操作未完成 · 原有数据保留")
        detail = str(exc)
        if isinstance(exc, sqlite3.Error):
            detail = "数据库暂时无法完成操作。请检查磁盘空间，关闭使用同一数据目录的其他软件窗口，然后重试。\n\n" + detail
        messagebox.showerror("操作未完成", detail, parent=self)

    def report_callback_exception(self, exc_type, exc, tb):
        import traceback
        log = self.app_home / "运行日志.txt"
        try:
            with log.open("a", encoding="utf-8") as stream:
                stream.write("\n" + datetime.now().isoformat() + "\n")
                traceback.print_exception(exc_type, exc, tb, file=stream)
        except OSError:
            pass
        self.report_error(exc)

    def refresh_projects(self):
        projects = self.store.projects()
        self.project_ids = [p["id"] for p in projects]
        self.project_list.delete(0, "end")
        for p in projects:
            self.project_list.insert("end", f"  {p['name']}  ·  {p['record_count']}")
        if self.current_project in self.project_ids:
            self.project_list.selection_set(self.project_ids.index(self.current_project))
        previous = self.vars["project"].get()
        self.project_options = {f"{p['name']}  [#{p['id']}]": p["id"] for p in projects}
        self.project_combo["values"] = list(self.project_options)
        if self.record_open and self.current_record:
            record = self.store.record(self.current_record)
            for label, pid in self.project_options.items():
                if pid == record["project_id"] and previous not in self.project_options:
                    self.loading = True
                    self.vars["project"].set(label)
                    self.loading = False
        count = len(self.store.records())
        self.stats_label.configure(text=f"{len(projects)} 个项目  /  {count} 条记录\n数据保存在本机")

    def refresh_records(self):
        try:
            records = self.store.records(self.current_project, self.query.get(), self.date_from.get(), self.date_to.get(), self.archived_view)
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.report_error(exc)
            return False
        self.records_tree.delete(*self.records_tree.get_children())
        for record in records:
            self.records_tree.insert("", "end", iid=str(record["id"]), values=(record["title"], record["note_date"]))
        self.count_label.configure(text=f"{len(records)} 条记录")
        scope = next((p["name"] for p in self.store.projects() if p["id"] == self.current_project), "全部实验记录")
        self.scope_label.configure(text="已归档记录" if self.archived_view else scope, wraplength=280)
        if self.current_record and self.records_tree.exists(str(self.current_record)):
            self.records_tree.selection_set(str(self.current_record))
        return True

    def _welcome(self):
        self._cancel_draft_job()
        self.records_tree.selection_remove(*self.records_tree.selection())
        self.record_open = False
        self.current_record = None
        self.dirty = False
        self.editor.pack_forget()
        self.empty.place(relx=.5, rely=.43, anchor="center")

    def _mark_dirty(self, *args):
        if not self.loading and self.record_open:
            self.dirty = True
            self.save_button.configure(text="保存记录 *")
            self.status_text.set("有未保存的修改 · Ctrl+S 保存")
            if self.draft_job is None:
                self.draft_job = self.after(1200, self._write_draft)

    def _editor_values(self):
        values = {k: variable.get() for k, variable in self.vars.items() if k != "project"}
        values["project_id"] = self.project_options.get(self.vars["project"].get())
        values.update({k: widget.get("1.0", "end-1c") for k, widget in self.texts.items()})
        return values

    def _cancel_draft_job(self):
        if self.draft_job is not None:
            self.after_cancel(self.draft_job)
            self.draft_job = None

    def _write_draft(self):
        self._cancel_draft_job()
        if not self.dirty or not self.record_open:
            return
        try:
            self.store.save_draft(self._editor_values(), self.current_record)
            self.status_text.set("本地草稿已保留 · Ctrl+S 正式保存记录")
        except (ValueError, OSError, sqlite3.Error):
            # 自动保留失败不弹出对话框打断输入，也不声称保存成功。
            self.status_text.set("草稿未能保留，请按 Ctrl+S 保存并检查磁盘空间")

    def _clear_draft(self):
        self.store.clear_draft()
        self._cancel_draft_job()
        self.pending_draft = None
        self.draft_button.configure(state="disabled")

    def recover_draft(self):
        if not self.pending_draft:
            return
        draft = self.pending_draft
        values = dict(draft["values"])
        record_id = draft["record_id"]
        if record_id is not None:
            try:
                saved = self.store.record(record_id)
                values.update(id=record_id, archived=saved["archived"])
            except ValueError:
                record_id = None
        self.current_record = record_id
        self._fill_editor(values)
        self.pending_draft = None
        self.draft_button.configure(state="disabled")
        self._mark_dirty()
        self.status_text.set(f"已恢复 {draft['saved_at']} 的草稿 · 请检查后保存")

    def repeat_record(self):
        if not self._saved_record():
            return
        previous = self.store.record(self.current_record)
        values = {k: previous[k] for k in ("project_id", "purpose", "equipment", "parameters", "steps", "tags")}
        values.update(title=(previous["title"][:190] + "（复测）"), note_date=date.today().isoformat())
        self.current_record = None
        self._fill_editor(values)
        self.notebook.select(0)
        self._mark_dirty()
        self.status_text.set("已复用实验条件 · 结果、问题和附件留空，请填写本次实验内容")

    def _modified(self, event):
        if event.widget.edit_modified():
            event.widget.edit_modified(False)
            self._mark_dirty()

    def _allow_leave(self):
        if self.pending_draft:
            answer = messagebox.askyesnocancel("发现未恢复的草稿", "上次有未保存的草稿。\n选择“是”恢复编辑；“否”放弃草稿；“取消”留在当前页面。", parent=self)
            if answer is None:
                return False
            if answer:
                self.recover_draft()
                return False
            self._clear_draft()
        if not self.dirty:
            return True
        answer = messagebox.askyesnocancel("保存当前修改？", "当前记录有未保存的内容。\n选择“是”保存；“否”放弃本次修改；“取消”继续编辑。", parent=self)
        if answer is None:
            return False
        if answer:
            return self.save_record()
        self._clear_draft()
        return True

    def _select_project(self, event):
        indices = self.project_list.curselection()
        if indices and indices[0] < len(self.project_ids):
            selected = self.project_ids[indices[0]]
            if selected != self.current_project or self.archived_view:
                self.choose_project(selected, False)

    def choose_project(self, project_id, archived=False):
        if not self._allow_leave():
            self.project_list.selection_clear(0, "end")
            if self.current_project in self.project_ids:
                self.project_list.selection_set(self.project_ids.index(self.current_project))
            return
        self.current_project, self.archived_view = project_id, archived
        self._welcome()
        self.refresh_projects()
        self.refresh_records()
        self.status_text.set("已归档记录可恢复，不会删除附件。" if archived else "选择已有记录，或新建一条实验记录。")

    def _select_record(self, event):
        selection = self.records_tree.selection()
        if not selection:
            return
        record_id = int(selection[0])
        if record_id == self.current_record:
            return
        if self._allow_leave():
            self.load_record(record_id)
        elif self.current_record and self.records_tree.exists(str(self.current_record)):
            self.records_tree.selection_set(str(self.current_record))
        else:
            self.records_tree.selection_remove(*selection)

    def _fill_editor(self, record):
        self._cancel_draft_job()
        if self.current_record is None:
            self.records_tree.selection_remove(*self.records_tree.selection())
        self.loading = True
        self.empty.place_forget()
        self.editor.pack(fill="both", expand=True)
        self.record_open = True
        for key in ("title", "note_date", "tags"):
            self.vars[key].set(record.get(key, ""))
        selected_project = record.get("project_id")
        self.vars["project"].set(next((label for label, pid in self.project_options.items() if pid == selected_project), ""))
        for key, text in self.texts.items():
            text.delete("1.0", "end")
            text.insert("1.0", record.get(key, ""))
            text.edit_modified(False)
            text.edit_reset()
        self.loading = False
        self.dirty = False
        self.save_button.configure(text="保存记录")
        self.archive_button.configure(text="恢复记录" if record.get("archived") else "归档")
        self.editor_label.configure(text="编辑实验记录" if record.get("id") else "新建实验记录")
        self.saved_label.configure(text=f"最近保存：{record.get('updated_at', '尚未保存')}  ·  Ctrl+S 保存")
        self.refresh_attachments()

    def load_record(self, record_id):
        try:
            record = self.store.record(record_id)
            self.current_record = record_id
            self._fill_editor(record)
            self.status_text.set(f"已打开：{record['title']}")
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.report_error(exc)

    def new_project(self):
        if not self._allow_leave():
            return
        dialog = ProjectDialog(self)
        if dialog.result:
            try:
                project = self.store.save_project(**dialog.result)
                self.current_project, self.archived_view = project, False
                self._welcome()
                self.refresh_projects()
                self.refresh_records()
                self.status_text.set("项目已创建，可以添加实验记录。")
                return project
            except (ValueError, OSError, sqlite3.Error) as exc:
                self.report_error(exc)
        return None

    def edit_project(self):
        if self.current_project is None:
            messagebox.showinfo("先选择项目", "请先点击左侧的一个项目，再修改项目设置。", parent=self)
            return
        if not self._allow_leave():
            return
        project = next(p for p in self.store.projects() if p["id"] == self.current_project)
        dialog = ProjectDialog(self, project)
        if dialog.result:
            try:
                self.store.save_project(**dialog.result, project_id=self.current_project)
                self.refresh_projects()
                self.refresh_records()
                self.status_text.set("项目设置已保存。")
            except (ValueError, OSError, sqlite3.Error) as exc:
                self.report_error(exc)

    def new_record(self):
        if not self._allow_leave():
            return
        if not self.project_options and self.new_project() is None:
            return
        project = self.current_project or next(iter(self.project_options.values()))
        self.current_record = None
        self._fill_editor({"project_id": project, "note_date": date.today().isoformat()})
        self.notebook.select(0)
        self.dirty = True
        self._mark_dirty()
        self.title_entry.focus_set()
        self.status_text.set("填写标题、参数和步骤后保存。")

    def save_record(self):
        if not self.record_open:
            return False
        values = self._editor_values()
        try:
            self.current_record = self.store.save_record(values, self.current_record)
            self._clear_draft()
            self.dirty = False
            self.refresh_projects()
            self.refresh_records()
            self._fill_editor(self.store.record(self.current_record))
            self.status_text.set("记录已保存到本机。")
            return True
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.report_error(exc)
            return False

    def search(self):
        if self._allow_leave() and self.refresh_records():
            self._welcome()
            self.status_text.set("筛选结果已更新，点击记录查看详情。")

    def clear_search(self):
        if not self._allow_leave():
            return
        self.query.set("")
        self.date_from.set("")
        self.date_to.set("")
        self._welcome()
        self.refresh_records()
        self.status_text.set("已清除筛选条件。")

    def export_list(self):
        if not self._allow_leave():
            return
        # 导出前重新应用输入框中的条件，列表和导出范围保持一致。
        if not self.refresh_records():
            return
        rows = self.store.records(self.current_project, self.query.get(), self.date_from.get(),
                                  self.date_to.get(), self.archived_view)
        if not rows:
            messagebox.showinfo("没有可导出的记录", "当前筛选没有记录，请调整筛选条件。", parent=self)
            return
        path = filedialog.asksaveasfilename(parent=self, title=f"导出当前筛选的 {len(rows)} 条记录",
            initialfile=f"实验记录汇总_{datetime.now():%Y%m%d_%H%M%S}.csv",
            defaultextension=".csv", filetypes=[("CSV表格", "*.csv")])
        if not path:
            return
        try:
            target = export_csv(rows, path)
            self.status_text.set(f"已导出 {len(rows)} 条记录：{target}")
            messagebox.showinfo("表格已导出", f"已导出 {len(rows)} 条已保存记录，可用 Excel 打开。\n表格包含附件数量，不包含附件文件。\n\n{target}", parent=self)
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.report_error(exc)

    def refresh_attachments(self):
        self.files_tree.delete(*self.files_tree.get_children())
        attachments = self.store.attachments(self.current_record) if self.current_record else []
        for item in attachments:
            self.files_tree.insert("", "end", iid=str(item["id"]), values=(item["original_name"], human_size(item["size"])))
        self.notebook.tab(2, text=f"附件 ({len(attachments)})")

    def _saved_record(self):
        if not self.record_open:
            messagebox.showinfo("先打开记录", "请先新建或打开一条实验记录。", parent=self)
            return False
        if self.dirty or self.current_record is None:
            return self.save_record()
        return True

    def add_files(self):
        if not self._saved_record():
            return
        paths = filedialog.askopenfilenames(title="选择要复制归档的附件", parent=self)
        completed, errors = 0, []
        for path in paths:
            try:
                self.store.add_attachment(self.current_record, path)
                completed += 1
            except (ValueError, OSError, sqlite3.Error) as exc:
                errors.append(f"{Path(path).name}：{exc}")
        self.refresh_attachments()
        self.status_text.set(f"已归档 {completed} 个附件，原始文件保留。")
        if errors:
            messagebox.showwarning("部分附件未归档", "\n".join(errors), parent=self)

    def open_attachment(self):
        selected = self.files_tree.selection()
        if not selected:
            messagebox.showinfo("选择附件", "请先选中一个附件。", parent=self)
            return
        try:
            path = self.store.attachment_path(int(selected[0]))
            # 非常见文档类型可能执行程序，打开前让使用者明确确认。
            safe = {".txt", ".csv", ".tsv", ".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".gif"}
            if path.suffix.lower() not in safe:
                if not messagebox.askyesno("打开此类附件？", "此文件将交给系统关联程序打开。\n请仅打开你信任的文件。是否继续？", parent=self):
                    return
            os.startfile(path)
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.report_error(exc)

    def archive(self):
        if not self._saved_record():
            return
        record = self.store.record(self.current_record)
        self.store.archive_record(self.current_record, not record["archived"])
        self._welcome()
        self.refresh_projects()
        self.refresh_records()
        self.status_text.set("记录已恢复。" if record["archived"] else "记录已归档，可在左侧“已归档记录”中恢复。")

    def export(self):
        if not self._saved_record():
            return
        record = self.store.record(self.current_record)
        clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", record["title"])[:65].strip(" .") or "实验记录"
        filename = f"记录报告_{clean}_{datetime.now():%Y%m%d_%H%M%S}.html"
        path = filedialog.asksaveasfilename(parent=self, title="导出实验记录报告", initialfile=filename,
            initialdir=str(self.app_home), defaultextension=".html", filetypes=[("网页报告", "*.html")])
        if not path:
            return
        try:
            target = export_report(self.store, self.current_record, path)
            self.status_text.set("报告已导出：" + str(target))
            if messagebox.askyesno("报告已导出", "报告已保存，可使用浏览器的打印功能。\n现在打开报告吗？", parent=self):
                webbrowser.open(target.as_uri())
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.report_error(exc)

    def backup(self):
        if not self._allow_leave():
            return
        path = filedialog.asksaveasfilename(parent=self, title="保存完整备份", initialdir=str(self.app_home),
            initialfile=f"实验记录备份_{datetime.now():%Y%m%d_%H%M%S}.zip", defaultextension=".zip", filetypes=[("完整备份", "*.zip")])
        if not path:
            return
        self.configure(cursor="watch")
        self.update_idletasks()
        try:
            result = self.store.create_backup(path)
            self.status_text.set("完整备份已保存：" + path)
            messagebox.showinfo("备份完成", f"已备份 {result['projects']} 个项目、{result['records']} 条记录、{result['attachments']} 个附件。\n\n{path}", parent=self)
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.report_error(exc)
        finally:
            self.configure(cursor="")

    def restore(self):
        if not self._allow_leave():
            return
        source = filedialog.askopenfilename(parent=self, title="选择本软件创建的完整备份", filetypes=[("完整备份", "*.zip")])
        if not source:
            return
        self.configure(cursor="watch")
        self.update_idletasks()
        try:
            info = inspect_backup(source)
            self.configure(cursor="")
            if not messagebox.askyesno("备份校验通过", f"备份包含 {info['projects']} 个项目、{info['records']} 条记录、{info['attachments']} 个附件。\n\n恢复到新目录并打开？当前数据目录会完整保留。", parent=self):
                return
            parent = filedialog.askdirectory(parent=self, title="选择存放恢复数据的父目录", initialdir=str(self.store.root.parent))
            if not parent:
                return
            destination = Path(parent) / f"恢复数据_{datetime.now():%Y%m%d_%H%M%S}_{os.urandom(2).hex()}"
            self.configure(cursor="watch")
            self.update_idletasks()
            restore_backup(source, destination)
            new_store = Store(destination)
            # 先确保配置可保存，再切换；任何失败都保留当前已打开的数据。
            try:
                restored_draft = new_store.read_draft()
                if self.config_path:
                    temporary = self.config_path.with_suffix(".new")
                    temporary.write_text(json.dumps({"data_dir": str(destination)}, ensure_ascii=False, indent=2), encoding="utf-8")
                    os.replace(temporary, self.config_path)
            except (OSError, ValueError, sqlite3.Error):
                new_store.close()
                raise
            old_store = self.store
            self.store = new_store
            old_store.close()
            self.current_project = None
            self.archived_view = False
            self.query.set("")
            self.date_from.set("")
            self.date_to.set("")
            self._welcome()
            self.refresh_projects()
            self.refresh_records()
            self.pending_draft = restored_draft
            self.draft_button.configure(state="normal" if self.pending_draft else "disabled")
            self.status_text.set("恢复完成 · " + ("备份含未保存草稿，可点击恢复草稿 · " if self.pending_draft else "") + str(destination))
            messagebox.showinfo("恢复完成", "已打开恢复的数据。原目录未改变。\n\n当前目录：" + str(destination)
                + ("\n\n本次使用了指定数据目录，下次启动需再次指定此路径。" if not self.config_path else ""), parent=self)
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.report_error(exc)
        finally:
            self.configure(cursor="")

    def demo(self):
        if not self._allow_leave():
            return
        try:
            project = seed_demo(self.store)
            self.choose_project(project)
            records = self.store.records(project)
            if records:
                self.current_record = None
                self.records_tree.selection_set(str(records[0]["id"]))
                self.load_record(records[0]["id"])
            self.status_text.set("演示数据已打开 · 所有示例均为虚构，不代表真实实验。")
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.report_error(exc)

    def help(self):
        dialog = tk.Toplevel(self)
        dialog.title("使用说明与数据位置")
        dialog.transient(self)
        dialog.geometry("690x560")
        text = tk.Text(dialog, wrap="word", padx=25, pady=22, font=(FONT, 11), bd=0)
        text.pack(fill="both", expand=True)
        text.insert("1.0", f"{APP_NAME} {VERSION}\n\n"
            "1. 创建项目：点击左侧“新建项目”，填写名称和研究目标。\n\n"
            "2. 记录实验：点击“新建实验记录”，填写标题、日期和内容。Ctrl+S保存。\n\n"
            "3. 添加附件：先保存记录，再在“附件”页添加文件。软件只复制文件，不移动或删除原件。\n\n"
            "4. 查找记录：输入关键词，必要时填写YYYY-MM-DD格式日期，然后点击查找。Ctrl+F定位搜索框。\n\n"
            "5. 导出报告：先保存记录，再点击“导出报告”。报告包含记录和附件清单，不包含附件实体。\n\n"
            "6. 备份与恢复：备份包含全部项目、记录和附件。恢复前会校验，恢复到新目录，原目录保留。建议定期把备份另存一份。\n\n"
            "7. 归档：记录归档后可在“已归档记录”中恢复，不删除附件。\n\n"
            "8. 草稿：编辑时约每1.2秒保留一次本地草稿。重新启动后可点击“恢复草稿”，检查后按Ctrl+S正式保存。\n\n"
            "9. 复用实验条件：保存当前记录后创建一条新记录，只带入目的、设备、参数、步骤和标签，结果与附件留空。\n\n"
            "10. 导出筛选结果：按当前项目、关键词、日期和归档状态导出CSV表格，可用Excel打开。\n\n"
            f"当前数据目录：\n{self.store.root}\n\n"
            "数据默认仅保存在本机。本软件不自动上传记录，也不自动验证实验结论。\n"
            "演示数据均明确标注为演示，不代表实际实验。")
        text.configure(state="disabled")
        ttk.Button(dialog, text="关闭", command=dialog.destroy).pack(pady=12)

    def close(self):
        if self._allow_leave():
            self.store.close()
            self.destroy()

    def destroy(self):
        self._cancel_draft_job()
        super().destroy()
