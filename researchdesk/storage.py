"""本地持久化、附件归档和经过校验的备份恢复。仅依赖Python标准库。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import uuid
import zipfile
from contextlib import closing
from datetime import date, datetime
from pathlib import Path, PurePosixPath

from . import APP_ID, VERSION


DB_NAME = "records.sqlite3"
MAX_ATTACHMENT = 100 * 1024 * 1024
MAX_BACKUP = 1024 * 1024 * 1024
RECORD_FIELDS = (
    "project_id", "title", "note_date", "purpose", "equipment", "parameters",
    "steps", "results", "issues", "tags",
)
TEXT_FIELDS = RECORD_FIELDS[1:]
ATTACHMENT_PATH = re.compile(r"attachments/[a-f0-9]{32}(?:\.[a-z0-9]{1,10})?\Z")


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_date(value: str) -> str:
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError("日期请填写为 YYYY-MM-DD，例如 2026-10-26。") from None
    return value


def _safe_path(root: Path, relative: str) -> Path:
    if not ATTACHMENT_PATH.fullmatch(relative):
        raise ValueError("附件保存路径不符合格式。")
    result = root / relative
    if not result.resolve().is_relative_to(root.resolve()) or result.is_symlink():
        raise ValueError("附件路径不能指向数据目录以外。")
    return result


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "attachments").mkdir(exist_ok=True)
        self.conn = sqlite3.connect(self.root / DB_NAME, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA trusted_schema=OFF")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS projects(
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, goal TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '进行中',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS records(
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id),
                title TEXT NOT NULL, note_date TEXT NOT NULL,
                purpose TEXT NOT NULL DEFAULT '', equipment TEXT NOT NULL DEFAULT '',
                parameters TEXT NOT NULL DEFAULT '', steps TEXT NOT NULL DEFAULT '',
                results TEXT NOT NULL DEFAULT '', issues TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '', archived INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS attachments(
                id INTEGER PRIMARY KEY,
                record_id INTEGER NOT NULL REFERENCES records(id),
                original_name TEXT NOT NULL, stored_path TEXT UNIQUE NOT NULL,
                size INTEGER NOT NULL, sha256 TEXT NOT NULL, added_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS records_project ON records(project_id, archived);
            CREATE INDEX IF NOT EXISTS attachments_record ON attachments(record_id);
        """)
        version = self.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if version and version[0] != "1":
            self.conn.close()
            raise ValueError("数据版本与当前软件不兼容。")
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO meta VALUES('schema_version','1')")

    def close(self):
        self.conn.close()

    def save_draft(self, values: dict, record_id=None):
        """草稿允许缺标题、缺日期；与正式记录分开保存，并随数据库备份。"""
        payload = {"record_id": record_id, "saved_at": now(),
                   "values": {k: values.get(k, "") for k in RECORD_FIELDS}}
        encoded = json.dumps(payload, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > 2 * 1024 * 1024:
            raise ValueError("草稿超过2MB，请保存记录或将大段原始资料作为附件添加。")
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('editor_draft',?)", (encoded,))

    def read_draft(self):
        row = self.conn.execute("SELECT value FROM meta WHERE key='editor_draft'").fetchone()
        if row is None:
            return None
        try:
            draft = json.loads(row[0])
            values = draft["values"]
            if (not isinstance(values, dict) or set(values) != set(RECORD_FIELDS)
                    or any(not isinstance(values[k], str) for k in TEXT_FIELDS)
                    or type(values["project_id"]) not in (int, type(None))
                    or type(draft["record_id"]) not in (int, type(None))
                    or not isinstance(draft["saved_at"], str)):
                raise ValueError()
            return draft
        except (ValueError, TypeError, KeyError):
            raise ValueError("草稿格式无法读取。原数据仍保留，请先备份再检查。") from None

    def clear_draft(self):
        with self.conn:
            self.conn.execute("DELETE FROM meta WHERE key='editor_draft'")

    def projects(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("""
            SELECT p.*, COUNT(CASE WHEN r.archived=0 THEN 1 END) AS record_count
            FROM projects p LEFT JOIN records r ON r.project_id=p.id
            GROUP BY p.id ORDER BY p.updated_at DESC, p.id DESC
        """)]

    def save_project(self, name: str, goal="", tags="", status="进行中", project_id=None) -> int:
        name = name.strip()
        if not name:
            raise ValueError("请填写项目名称。")
        if len(name) > 150:
            raise ValueError("项目名称请控制在150个字以内。")
        if status not in ("进行中", "已完成", "暂停"):
            raise ValueError("项目状态无效。")
        stamp = now()
        with self.conn:
            if project_id is None:
                result = self.conn.execute(
                    "INSERT INTO projects(name,goal,tags,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                    (name, goal.strip(), tags.strip(), status, stamp, stamp),
                )
                return result.lastrowid
            result = self.conn.execute(
                "UPDATE projects SET name=?,goal=?,tags=?,status=?,updated_at=? WHERE id=?",
                (name, goal.strip(), tags.strip(), status, stamp, project_id),
            )
            if not result.rowcount:
                raise ValueError("没有找到此项目。")
        return project_id

    def save_record(self, values: dict, record_id=None) -> int:
        fields = {k: str(values.get(k, "")).strip() for k in TEXT_FIELDS}
        if not fields["title"]:
            raise ValueError("请填写实验记录标题。")
        if len(fields["title"]) > 200:
            raise ValueError("记录标题请控制在200个字以内。")
        validate_date(fields["note_date"])
        try:
            fields["project_id"] = int(values.get("project_id", 0))
        except (ValueError, TypeError):
            raise ValueError("请选择所属项目。") from None
        if not self.conn.execute("SELECT id FROM projects WHERE id=?", (fields["project_id"],)).fetchone():
            raise ValueError("请选择已存在的项目。")
        params = [fields[k] for k in RECORD_FIELDS]
        stamp = now()
        with self.conn:
            if record_id is None:
                result = self.conn.execute(
                    f"INSERT INTO records({','.join(RECORD_FIELDS)},created_at,updated_at) "
                    f"VALUES({','.join('?' for _ in RECORD_FIELDS)},?,?)", params + [stamp, stamp],
                )
                record_id = result.lastrowid
            else:
                result = self.conn.execute(
                    f"UPDATE records SET {','.join(k+'=?' for k in RECORD_FIELDS)},updated_at=? WHERE id=?",
                    params + [stamp, record_id],
                )
                if not result.rowcount:
                    raise ValueError("没有找到此记录。")
            self.conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (stamp, fields["project_id"]))
        return record_id

    def record(self, record_id: int) -> dict:
        row = self.conn.execute("""SELECT r.*, p.name AS project_name FROM records r
            JOIN projects p ON p.id=r.project_id WHERE r.id=?""", (record_id,)).fetchone()
        if not row:
            raise ValueError("没有找到此记录。")
        return dict(row)

    def records(self, project_id=None, query="", date_from="", date_to="", archived=False) -> list[dict]:
        conditions, params = ["r.archived=?"], [int(archived)]
        if project_id is not None:
            conditions.append("r.project_id=?")
            params.append(project_id)
        for value, symbol in ((date_from.strip(), ">="), (date_to.strip(), "<=")):
            if value:
                validate_date(value)
                conditions.append(f"r.note_date{symbol}?")
                params.append(value)
        if date_from and date_to and date_from > date_to:
            raise ValueError("开始日期不能晚于结束日期。")
        if query.strip():
            # LIKE中的通配符按字面匹配，用户无需了解SQL语法。
            literal = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            columns = ["p.name", "p.tags"] + [f"r.{k}" for k in TEXT_FIELDS if k != "note_date"]
            conditions.append("(" + " OR ".join(f"{c} LIKE ? ESCAPE '\\'" for c in columns) + ")")
            params.extend([f"%{literal}%"] * len(columns))
        return [dict(r) for r in self.conn.execute(
            "SELECT r.*, p.name AS project_name, "
            "(SELECT COUNT(*) FROM attachments a WHERE a.record_id=r.id) AS attachment_count "
            "FROM records r JOIN projects p ON p.id=r.project_id WHERE " + " AND ".join(conditions)
            + " ORDER BY r.note_date DESC, r.id DESC", params,
        )]

    def archive_record(self, record_id: int, archived=True):
        with self.conn:
            result = self.conn.execute("UPDATE records SET archived=?,updated_at=? WHERE id=?",
                                       (int(archived), now(), record_id))
            if not result.rowcount:
                raise ValueError("没有找到此记录。")

    def attachments(self, record_id: int) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM attachments WHERE record_id=? ORDER BY id", (record_id,))]

    def add_attachment(self, record_id: int, source: str | Path) -> int:
        self.record(record_id)
        source = Path(source)
        if not source.is_file():
            raise ValueError("请选择一个存在的文件。")
        if source.stat().st_size > MAX_ATTACHMENT:
            raise ValueError("单个附件最大100MB，请选择较小的文件。")
        extension = source.suffix.lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,10}", extension):
            extension = ""
        relative = f"attachments/{uuid.uuid4().hex}{extension}"
        destination = _safe_path(self.root, relative)
        # 先完整复制，再登记到数据库；读取和备份不会看到尚未写完的附件。
        with source.open("rb") as src, destination.open("xb") as dst:
            shutil.copyfileobj(src, dst)
        size = destination.stat().st_size
        if size > MAX_ATTACHMENT:
            raise ValueError("复制期间文件增大，超出100MB附件限制，请重试。")
        file_digest = digest(destination)
        with self.conn:
            result = self.conn.execute("""INSERT INTO attachments
                (record_id,original_name,stored_path,size,sha256,added_at) VALUES(?,?,?,?,?,?)""",
                (record_id, source.name, relative, size, file_digest, now()))
        return result.lastrowid

    def attachment_path(self, attachment_id: int) -> Path:
        row = self.conn.execute("SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone()
        if not row:
            raise ValueError("没有找到此附件。")
        path = _safe_path(self.root, row["stored_path"])
        if not path.is_file():
            raise ValueError("附件文件缺失，请检查数据目录或从备份恢复。")
        if path.stat().st_size != row["size"] or digest(path) != row["sha256"]:
            raise ValueError("附件内容与归档时不同，请检查文件或从备份恢复。")
        return path

    def create_backup(self, destination: str | Path) -> dict:
        destination = Path(destination).resolve()
        if destination.exists():
            raise ValueError("目标文件已存在，请使用新的备份文件名。")
        if destination.is_relative_to((self.root / "attachments").resolve()):
            raise ValueError("备份不能放进软件管理的附件目录。")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="research-backup-") as temp:
            snapshot = Path(temp) / DB_NAME
            with closing(sqlite3.connect(snapshot)) as target:
                self.conn.backup(target)
            details = _inspect_database(snapshot)
            paths = [(DB_NAME, snapshot)]
            for attachment in details.pop("attachment_rows"):
                file = _safe_path(self.root, attachment["stored_path"])
                if (not file.is_file() or file.stat().st_size != attachment["size"]
                        or digest(file) != attachment["sha256"]):
                    raise ValueError("附件缺失或已改变，备份未完成：" + attachment["original_name"])
                paths.append((attachment["stored_path"], file))
            if sum(p.stat().st_size for _, p in paths) > MAX_BACKUP:
                raise ValueError("第一版备份最大支持1GB，请先分项目整理数据。")
            manifest = {"app_id": APP_ID, "format_version": 1, "software_version": VERSION,
                        "created_at": now(), **details, "files": [
                            {"path": rel, "size": file.stat().st_size, "sha256": digest(file)}
                            for rel, file in paths]}
            # 先生成完整ZIP，再以不覆盖的方式交付目标文件。
            staging = Path(temp) / "complete.zip"
            with zipfile.ZipFile(staging, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                for relative, file in paths:
                    archive.write(file, relative)
            with staging.open("rb") as src, destination.open("xb") as dst:
                shutil.copyfileobj(src, dst)
        return manifest


def _inspect_database(path: Path) -> dict:
    """备份只接受已知的数据格式；不执行归档数据库中可能附带的触发器或视图。"""
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA trusted_schema=OFF")
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("数据库完整性检查未通过。")
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view')").fetchone():
                raise ValueError("备份含有不支持的数据库对象。")
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables != {"meta", "projects", "records", "attachments"}:
                raise ValueError("备份数据库格式不匹配。")
            if conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] != "1":
                raise ValueError("备份数据版本不兼容。")
            if conn.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("备份中的项目或记录关联不完整。")
            for table, expected in {
                "projects": {"id", "name", "goal", "tags", "status", "created_at", "updated_at"},
                "records": {"id", *RECORD_FIELDS, "archived", "created_at", "updated_at"},
                "attachments": {"id", "record_id", "original_name", "stored_path", "size", "sha256", "added_at"},
                "meta": {"key", "value"},
            }.items():
                if {r[1] for r in conn.execute(f"PRAGMA table_info({table})")} != expected:
                    raise ValueError("备份数据库字段不匹配。")
            rows = [dict(r) for r in conn.execute("SELECT * FROM attachments")]
            for row in rows:
                if (not ATTACHMENT_PATH.fullmatch(row["stored_path"])
                        or not isinstance(row["size"], int) or not 0 <= row["size"] <= MAX_ATTACHMENT
                        or not re.fullmatch(r"[a-f0-9]{64}", row["sha256"])):
                    raise ValueError("备份中的附件信息无效。")
            return {"projects": conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
                    "records": conn.execute("SELECT COUNT(*) FROM records").fetchone()[0],
                    "attachments": len(rows), "attachment_rows": rows}
    except (sqlite3.Error, TypeError, IndexError, KeyError) as exc:
        raise ValueError("无法读取备份数据库，可能已损坏或格式不兼容。") from exc


def _manifest(archive: zipfile.ZipFile) -> dict:
    entries = archive.infolist()
    names = [e.filename for e in entries]
    if len(names) != len(set(names)) or len(names) > 10002:
        raise ValueError("备份存在重复文件或文件数量过多。")
    if "manifest.json" not in names:
        raise ValueError("这不是本软件创建的备份：缺少清单。")
    if sum(e.file_size for e in entries) > MAX_BACKUP:
        raise ValueError("备份解压大小超过1GB限制。")
    for entry in entries:
        if (entry.flag_bits & 1 or stat.S_ISLNK(entry.external_attr >> 16)
                or entry.filename not in ("manifest.json", DB_NAME)
                and not ATTACHMENT_PATH.fullmatch(entry.filename)):
            raise ValueError("备份包含不允许的路径或文件类型。")
    if archive.getinfo("manifest.json").file_size > 4 * 1024 * 1024:
        raise ValueError("备份清单过大。")
    try:
        manifest = json.loads(archive.read("manifest.json"))
        if manifest["app_id"] != APP_ID or manifest["format_version"] != 1:
            raise ValueError("备份不属于本软件或版本不兼容。")
        files = manifest["files"]
        declared = [f["path"] for f in files]
        if (len(declared) != len(set(declared)) or set(declared) != set(names) - {"manifest.json"}
                or DB_NAME not in declared):
            raise ValueError("备份清单和文件不一致。")
        for item in files:
            if (type(item["size"]) is not int or item["size"] < 0
                    or archive.getinfo(item["path"]).file_size != item["size"]
                    or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"])):
                raise ValueError("备份文件信息不正确。")
        return manifest
    except (KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("备份清单格式不正确。") from exc


def _extract_verified(source: str | Path, stage: Path) -> dict:
    try:
        with zipfile.ZipFile(source) as archive:
            manifest = _manifest(archive)
            for item in manifest["files"]:
                output = stage / PurePosixPath(item["path"])
                if not output.resolve().is_relative_to(stage.resolve()):
                    raise ValueError("备份路径超出恢复目录。")
                output.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item["path"]) as src, output.open("xb") as dst:
                    shutil.copyfileobj(src, dst)
                if digest(output) != item["sha256"]:
                    raise ValueError("备份校验失败，文件可能已损坏。")
            details = _inspect_database(stage / DB_NAME)
            paths = {r["stored_path"] for r in details["attachment_rows"]}
            if paths != {i["path"] for i in manifest["files"] if i["path"] != DB_NAME}:
                raise ValueError("附件清单与数据库引用不一致。")
            for row in details["attachment_rows"]:
                path = _safe_path(stage, row["stored_path"])
                if path.stat().st_size != row["size"] or digest(path) != row["sha256"]:
                    raise ValueError("附件与数据库校验信息不一致。")
            for key in ("projects", "records", "attachments"):
                if manifest[key] != details[key]:
                    raise ValueError("备份统计与实际内容不一致。")
            return manifest
    except (zipfile.BadZipFile, RuntimeError, EOFError) as exc:
        raise ValueError("备份不是有效的ZIP文件，或文件已损坏。") from exc


def inspect_backup(source: str | Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="research-inspect-") as temp:
        return _extract_verified(source, Path(temp))


def restore_backup(source: str | Path, destination: str | Path) -> dict:
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError("恢复需要一个尚不存在的新目录，原有数据不会被覆盖。")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="research-restore-", dir=destination.parent) as temp:
        stage = Path(temp) / "data"
        stage.mkdir()
        manifest = _extract_verified(source, stage)
        # 验证通过后复制到全新目录。复制失败时不切换当前数据，原目录始终保留。
        # 不依赖目录重命名权限，也适用于受限目录及不同存储设备。
        shutil.copytree(stage, destination)
    return manifest
