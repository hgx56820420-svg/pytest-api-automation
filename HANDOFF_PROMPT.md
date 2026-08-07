# 换机器后给 AI 助手的启动提示词

> 复制下面这段话粘贴给新会话里的 AI（Kiro/Comate 等），它会自动读取项目里的
> `HANDOFF.md` 和 `question.md` 来对齐上下文。

---

## 提示词（直接复制）

```
我在做一个 pytest 接口自动化测试的求职作品集项目，仓库已经 clone 到本地。
请先读这两个文件了解背景：
1. HANDOFF.md —— 项目背景、6 阶段路线图、当前卡在哪一步、环境踩坑记录
2. question.md —— 我学过的知识点和踩过的坑的详细笔记

关键协作原则（请严格遵守）：
- 这是我的学习项目，测试代码必须由我自己写，你只负责讲原理、给思路、
  review 我写的代码、指出错误在哪里和为什么错——不要直接把完整代码写好发给我
- 被测服务（app/ 目录下的 FastAPI 代码）是你之前写的，不用再改，
  除非我明确说要扩展被测服务
- 每完成一个小块功能，帮我验证测试真的跑通（用终端跑 pytest），
  并且尽量做"故意让它失败"的反向验证，证明校验逻辑本身有意义
- 我们在用 git，每个阶段性成果要 commit + push，commit message 要清楚
- 我的环境是 Windows + PowerShell 5.1，不支持 `&&`，多条命令用 `;` 分隔
- 服务用 8010 端口启动：.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010

请先读完两份文档，用 2-3 句话总结你理解的当前进度，然后告诉我下一步该做什么，
不要一次性讲太多内容，一步一步带我走。
```

---

## 备用：如果只想快速恢复环境，不需要 AI 重新理解全部背景

```powershell
git clone https://github.com/hgx56820420-svg/pytest-api-automation.git
cd pytest-api-automation
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install faker
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

新开一个终端窗口跑测试确认环境没问题：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_auth.py -v
```

预期看到 `8 passed`。如果不是，先把报错信息发给 AI，让它帮你排查环境问题，
再继续往下做功能。

---

## 当前最需要接上的任务（一句话版）

`tests/test_products.py` 是空文件，下一步要写 `test_create_product_success`，
详细骨架和步骤在 `HANDOFF.md` 第六节"当前卡在哪一步"里，直接照着做。
