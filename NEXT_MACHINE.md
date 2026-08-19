# 下一台机器使用说明

## 恢复环境

```powershell
git clone https://github.com/hgx56820420-svg/pytest-api-automation.git
cd pytest-api-automation
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

启动服务：

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

另开终端验证：

```powershell
.venv\Scripts\python.exe -m pytest tests/ -v
```

## Allure 报告

项目使用 Node.js 安装 Allure 命令行工具；Allure 还需要 Java 21 或更高版本：

```powershell
npm install
```

`requirements.txt` 已包含 `allure-pytest`，所以 Python 依赖安装完成后，pytest 就能识别
`--alluredir` 参数。

如果系统没有 Java，可安装 JDK 21。安装后验证：

```powershell
java -version
node_modules\.bin\allure.cmd --version
```

生成并打开报告：

```powershell
.\scripts\run_allure.ps1
```

脚本会为当前 PowerShell 进程设置项目内 Java、重新生成 `allure-results` 和
`allure-report`，再用本地 HTTP 服务打开报告。不要直接双击 `allure-report/index.html`。

## 接手原则

- `STATUS.yaml` 是当前状态的首要来源，但要用 Git 和测试命令复核。
- `question.md` 是已提交的学习记录；本地 `archive/` 中的材料仅作历史参考，不保证代表当前状态。
- 每次阶段性完成后更新 `STATUS.yaml` 的日期、任务和验证结果，再单独提交。
