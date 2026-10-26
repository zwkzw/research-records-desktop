# 实验记录与科研文件管理软件

Research Records Desktop：面向个人研究者的中文本地实验记录工具。使用 Python、Tkinter 和 SQLite，无需服务器、账号或付费 API。

当前版本：**V1.0 开源预览版**。主要面向 Windows 10/11 64 位环境；其他系统尚未验证。项目使用 AI 辅助开发。

## 功能

- 项目管理：研究目标、标签和项目状态。
- 实验记录：目的、设备、参数、步骤、结果及后续计划。
- 草稿恢复：编辑期间自动保留本地草稿，重开后恢复。
- 重复实验：复用实验条件，清空结果和附件，独立保存本次记录。
- 附件归档：复制原始文件，保留原件，使用 SHA-256 核对完整性。
- 搜索筛选：项目、关键词、日期范围和归档状态。
- 导出：可打印的 HTML 报告、Excel 可读取的中文 CSV 汇总。
- 备份恢复：SQLite 一致性快照与附件完整备份，校验后恢复到新目录。

## 快速运行

安装带有 Tkinter 的 Python 3.11，然后在项目目录运行：

```powershell
python main.py
```

运行时仅依赖 Python 标准库，无需安装第三方 Python 包。可先点击“导入演示”体验功能，所有示例都明确标为演示。

使用独立目录试用：

```powershell
python main.py --data-dir ./demo-data --demo
```

程序正常启动时默认将数据保存在程序旁的 `用户数据` 目录。请使用可写入的普通文件夹。恢复备份后，实际数据位置可在“使用说明”中查看。

完整操作步骤见 [使用说明](使用说明.md)。

## 测试

```powershell
python -m unittest discover -s tests -v
```

测试使用临时目录，不读取用户实验资料。部分测试会创建 Tk 窗口，因此需要可用的桌面环境。预览版开发时通过了 27 项行为与界面集成测试，以及 8 组打包运行检查；实际屏幕缩放、中文输入和不同 Windows 环境仍需试用验证。

## 构建 Windows 便携包

在 Windows 上执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-build.txt
.\.venv\Scripts\python tools/build_release.py
```

产物位于 `releases/preview-日期时间/`。完整解压 ZIP 后运行 `ResearchRecords.exe`，保留同目录的 `_internal` 文件夹。构建脚本拒绝覆盖已有发布目录。

## 数据与当前限制

- 本地单用户模式，同一数据目录请只用一个软件窗口编辑。
- 数据和备份未加密；不要将实验数据库、附件或备份提交到公共仓库。
- 单个附件最大 100 MB，完整备份最大 1 GB；大备份操作期间界面可能等待。
- 草稿与正式记录分开保存，正式保存仍需按 Ctrl+S；草稿不能代替备份。
- HTML/CSV 导出包含记录或附件清单，不包含附件实体；迁移完整资料请使用备份恢复。
- 软件保存输入内容，不自动验证实验结论。

## 项目结构

```text
main.py                   应用入口
researchdesk/ui.py        中文桌面界面
researchdesk/storage.py   数据、草稿、附件和备份
researchdesk/reports.py   HTML 报告
researchdesk/exports.py   CSV 汇总
researchdesk/demo.py      明确标注的演示资料
tests/                    行为与界面集成测试
tools/build_release.py    Windows 便携包构建
```

## 贡献与许可

欢迎提交 Issue 或 Pull Request。问题反馈请附复现步骤、软件版本和系统环境，不要附上密码、个人信息或未脱敏实验资料。提交代码前请运行上述测试。

项目代码以 [MIT License](LICENSE) 开源。第三方运行时与构建工具保留各自许可，见 [第三方组件说明](第三方组件说明.md)。
