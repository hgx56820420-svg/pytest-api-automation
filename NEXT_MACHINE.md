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

如果系统没有 Java，可安装 JDK 21。安装后验证：

```powershell
java -version
node_modules\.bin\allure.cmd --version
```

生成并打开报告：

```powershell
.venv\Scripts\python.exe -m pytest tests/ --alluredir allure-results
node_modules\.bin\allure.cmd serve allure-results
```

## 让 AI 接手

把下面这段作为新会话第一条消息：

```text
这是 pytest-api-automation 学习项目。请先读取 AGENTS.md、STATUS.yaml、PROJECT.md，
然后执行 git status --short，并用 2-3 句话总结真实当前状态。
测试代码默认由我本人编写；你负责讲原理、拆任务、review 和验证，不要直接代写完整测试。
被测服务在 app/，默认不要修改。当前任务以 STATUS.yaml 的 next_task 为准，完成小步后运行 pytest。
环境是 Windows PowerShell，服务端口 8010，测试使用 .venv\Scripts\python.exe。
```

## 接手原则

- `STATUS.yaml` 是当前状态的首要来源，但要用 Git 和测试命令复核。
- `交接文档.md`、`HANDOFF.md`、`question.md` 是历史和教学资料，不保证全部代表当前状态。
- 每次阶段性完成后更新 `STATUS.yaml` 的日期、任务和验证结果，再单独提交。
