# 交接文档 — pytest-api-automation

> 最后更新：2026-08-07
> 换机器后，把这份文档从头读一遍即可无缝接上。

---

## 一、这个项目是什么

**目标**：做一个求职面试用的作品集项目——Python + pytest 的接口自动化测试框架。

**关键约束（很重要，别忘）**：
- 作品的价值不在代码量，在**你能讲清楚每一层为什么这么设计**
- 所以协作方式是：AI 讲原理 + 给骨架 + code review，**测试代码由你本人写**
- 唯一例外：被测服务（`app/`）由 AI 写，因为它不是学习目标，你只需读懂

**仓库**：https://github.com/hgx56820420-svg/pytest-api-automation

---

## 二、6 个阶段的路线图（当前进度：阶段 1 已完成）

| 阶段 | 内容 | 谁写 | 状态 |
|---|---|---|---|
| 1 | FastAPI 被测服务（靶子） | AI | 已完成 |
| 2 | 裸写第一个测试（故意写丑，体会痛点） | **你** | 已完成 |
| 3 | 封装 HTTP 客户端 + conftest fixture + 多环境配置 | **你** | 已完成 |
| 4 | 数据驱动（parametrize）+ 响应结构校验 + faker 造数 | **你** | 进行中（见下方"当前进度"） |
| 5 | Allure 报告 + GitHub Actions CI | **你** | 未开始 |
| 6 | README（含架构图和设计决策说明） | **你** | 未开始 |

阶段 2 的用意必须记住：**先用最笨的写法踩一遍坑**（URL 硬编码、每个用例重新登录、断言散乱），
这样阶段 3 的每一层封装你才知道是在解决什么真实问题，面试时才讲得出来。
不要跳过阶段 2 直接抄一个漂亮框架。

### 阶段 2 完成情况（2026-08-06）

`tests/test_auth.py` 写了 5 条用例，全部通过：
- `test_register_success` — 注册成功 201
- `test_register_duplicate_username` — 同用户名二次注册 409
- `test_login_success` — 注册后登录，断言 200 且响应含 `access_token`
- `test_login_wrong_password` — 登录密码故意传错，401
- `test_me_without_token` — 不带 token 访问 `/me`，401

**踩过的坑（真实记录，别再犯）**：
- 忘记保存文件 → pytest 显示 `collected 0 items` / `no tests ran`。VS Code 里开自动保存（`Ctrl+Shift+P` → `Preferences: Open Settings (UI)` → 搜 `files.autoSave` → 选 `afterDelay`）。
- 复制代码时缩进错位，函数体掉出函数外，变成模块顶层代码，pytest 收集直接失败。
- 同名函数重复定义（写了两个 `test_register_success`），Python 会静默用后面的覆盖前面的，前一个测试形同没写。
- **最容易犯的语义错误**：注册和登录复用同一个 `request_body`，导致"密码错误"用例实际传的是正确密码，断言必然不符。教训：**每次改变了输入条件，就要用新变量名装新数据，不要复用旧字典**。

**用户反馈的最大痛点**：每条需要登录态的用例都要重新走一遍"注册 → 拿 token"，这正是阶段 3 要用 fixture 解决的问题。

---

## 三、换机器后的环境准备

### 1. 拉代码

```powershell
git clone https://github.com/hgx56820420-svg/pytest-api-automation.git
cd pytest-api-automation
```

### 2. 建虚拟环境并装依赖

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 3. 启动被测服务

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

启动后打开 http://127.0.0.1:8010/docs 能看到 Swagger UI 就算成功。
这个页面可以直接点按钮调接口，是你熟悉被测对象最快的方式。

---

## 四、环境上的坑（都是已经踩过的，别重复踩）

1. **PowerShell 5.1 不支持 `&&`**
   多条命令用 `;` 分隔。`git add -A && git commit -m "x"` 会报
   `标记"&&"不是此版本中的有效语句分隔符`。

2. **端口 8000 在原来那台机器上被别的程序占用了**
   所以本项目统一用 **8010**。如果新机器上 8010 也被占，换一个并同步改测试配置。
   症状是 uvicorn 报 `[Errno 10048] ... 只允许使用一次`，
   而且请求会打到那个占用端口的程序上，表现为莫名的 404 / 405。

3. **Python 3.14 + pydantic 版本问题**
   `pydantic==2.11.7` 依赖的 `pydantic-core==2.33.2` 没有 cp314 预编译包，
   pip 会尝试从源码编译，需要下载 Rust 工具链，无网络时直接失败。
   `requirements.txt` 里已经锁到 `pydantic==2.13.4`，**不要往下降版本**。

4. **密码哈希用的是标准库 pbkdf2，不是 bcrypt**
   这是刻意的选择，避免 bcrypt 在 Windows 上编译踩坑。

---

## 五、被测服务代码导读（阶段 1 产物）

```
app/
├── config.py      配置（JWT 密钥、token 有效期、数据库地址、初始余额）
├── database.py    SQLAlchemy engine / session / get_db 依赖
├── models.py      三张表：User / Product / Order
├── schemas.py     请求与响应的 Pydantic 契约（参数校验规则都在这里）
├── auth.py        密码哈希 + JWT 签发/校验 + get_current_user 依赖
├── main.py        服务入口，挂载三个路由 + /health
└── routers/
    ├── auth.py        注册 / 登录 / 查当前用户
    ├── products.py    商品列表 / 详情 / 创建 / 更新 / 下架
    └── orders.py      下单 / 我的订单 / 详情 / 支付 / 取消
```

### 读代码的建议顺序

1. `schemas.py` — 先看契约，知道每个接口收什么、返回什么
2. `routers/auth.py` — 最简单的模块，理解 FastAPI 路由的基本写法
3. `auth.py` 的 `get_current_user` — 理解 Token 鉴权是怎么串进每个接口的
4. `routers/orders.py` — 业务最厚的一块，看下单的四重校验和取消的回滚逻辑

### 14 个接口清单

**认证** `/api/auth`
- `POST /register` — 201；用户名重复 409；username<3位 或 password<6位 → 422
- `POST /login` — 200 返回 access_token；账号或密码错 → 401
- `GET /me` — 需 Token；无/错/过期 Token → 401

**商品** `/api/products`
- `GET ""` — 分页列表，支持 `page`/`size`/`keyword`/`status`
- `GET /{id}` — 详情；不存在 404
- `POST ""` — 201，需 Token
- `PUT /{id}` — 部分更新（只改传了的字段）
- `DELETE /{id}` — 软删除，status 改成 off_sale，记录不消失

**订单** `/api/orders`
- `POST ""` — 201；商品不存在 404；已下架/库存不足/余额不足 → 400；quantity<=0 → 422
- `GET ""` — 我的订单列表，支持 status 筛选
- `GET /{id}` — 详情；别人的订单也返回 404（不泄露存在性）
- `POST /{id}/pay` — created→paid；非 created 状态 → 409
- `POST /{id}/cancel` — created→cancelled，**并回滚库存和余额**；非 created → 409

**其他**
- `GET /health` — 给 CI 轮询等待服务就绪用

### 服务里刻意埋好的测试点

写测试时优先覆盖这些，它们是让用例集显得专业的关键：

- **鉴权链路**：不带 token / 格式错 / 签名错 / 过期 / 用户已删 → 全部 401
- **库存边界**：stock=1 时下单 2 个 → 400
- **余额边界**：余额刚好等于金额 → 应该成功；差 0.01 → 400
- **状态机非法流转**：已支付的订单再取消 → 409
- **参数校验**：`page=0`、`size=-1`、`size=101`、`quantity=0` → 422
- **越权访问**：用 A 的 token 查 B 的订单 → 404
- **副作用与回滚**（最有价值的一类）：
  取消订单后要断言**库存回到原值、余额回到原值**，而不只是断言响应体的 status 字段。
  绝大多数候选人只断言 response，能断言副作用是理解力的分水岭。

### 已验证的行为

阶段 1 结束时跑过一轮 35 个检查点的冒烟验证，全部通过，包含：
- 14 个接口的正常路径
- 401 / 404 / 409 / 400 / 422 各类异常路径
- 取消订单后的库存与余额回滚（stock 1→3，balance 980.0→1000.0）

这个冒烟脚本是临时跑的、没有落盘。**阶段 2 你要用 pytest 正式重写它**，
这也正好是你的第一个练手任务。

---

## 六、当前进度（阶段 4，2026-08-07）

### 已完成并验证通过的内容

`tests/test_auth.py`（8 条用例全过）：
- `test_register_success` 用 `@pytest.mark.parametrize` 对 username 长度做边界值测试
  (2/3/20/21 位 → 422/201/201/422)，直接对应 `app/schemas.py` 的 `min_length=3, max_length=20`
- `test_register_duplicate_username`、`test_login_success`、`test_login_wrong_password`、
  `test_me_without_token` 复用 `conftest.py` 里的 `base_url` / `registered_user` fixture
- `test_login_success` 用 `jsonschema` 的 `TOKEN_SCHEMA` 校验响应结构，
  不再是简单的 `"access_token" in response.json()`

`conftest.py` 里已有：
- `base_url` fixture（读环境变量 `API_BASE_URL`，读不到用默认值 `127.0.0.1:8010`）
- `registered_user` fixture（注册一个新用户，返回 username/password）
- `TOKEN_SCHEMA`（jsonschema 格式的登录响应结构定义）

已装并写入 `requirements.txt` 的测试依赖：`pytest`、`requests`、`jsonschema`、`Faker`。

### 当前卡在哪一步（下次打开电脑先做这个）

**`tests/test_products.py` 和 `tests/test_order.py` 都还是空文件，一行代码都没写。**

上一轮我讲了思路和一个示例代码块，但你还没有动手落地。这是下次继续时的起点：

**任务：给 `POST /api/products` 写第一条测试（`test_create_product_success`）**

需要覆盖三件新东西：
1. 这个接口需要登录（看 `app/routers/products.py` 里 `_: User = Depends(get_current_user)`），
   所以测试里要先拿 `registered_user`，再调 `/api/auth/login` 换成 token
2. 带 token 的请求要在 `headers` 里加 `{"Authorization": f"Bearer {token}"}`——
   这是你项目里第一次真正用到鉴权请求头，之前的接口都不需要登录
3. 商品的 `name`/`price`/`stock` 用 `faker` 生成（`fake.word()`、`fake.pyfloat(...)`、
   `fake.random_int(...)`），不要手写固定值，理由和阶段 2 教训一致：固定值多次运行容易冲突或显得不真实

示例骨架（可以直接抄这个开始改）：

```python
import requests
from faker import Faker

fake = Faker()


def test_create_product_success(base_url, registered_user):
    login_response = requests.post(
        base_url + "/api/auth/login",
        json=registered_user,
        timeout=5,
    )
    token = login_response.json()["access_token"]

    product_body = {
        "name": fake.word(),
        "price": fake.pyfloat(min_value=1, max_value=1000, right_digits=2),
        "stock": fake.random_int(min=1, max=100),
    }

    response = requests.post(
        base_url + "/api/products",
        json=product_body,
        headers={"Authorization": f"Bearer {token}"},
        timeout=5,
    )
    assert response.status_code == 201, response.text
```

写完跑：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_products.py -v
```

跑通之后，下一步是把"登录拿 token"这个动作也抽成一个 fixture（跟 `registered_user` 一样的思路），
因为商品/订单模块几乎每条用例都需要 token,重复写"注册 → 登录 → 取 token"会跟阶段 2 一样烦。
这个 fixture 还没讨论具体怎么设计，属于下一步要一起想的内容。

### 阶段 4 剩余的完整范围（做完 test_products 第一条后继续往下推）

参考 `HANDOFF.md` 第五节"服务里刻意埋好的测试点"和 `app/routers/orders.py` 的业务逻辑，
阶段 4 完整要覆盖的场景大致是：

- 商品：创建成功 / 价格非法(≤0) / 无 token / 分页参数边界(page=0, size=101)
- 订单：下单成功 / 库存不足 / 余额不足 / 商品不存在 / quantity 非法 / 状态机非法流转(pay 两次/cancel 已支付)
- 订单副作用：取消订单后断言库存和余额真的回滚（这是 `HANDOFF.md` 里反复强调的"最有价值的一类"用例）

这部分具体怎么拆分成测试用例、要不要每个场景都写，等你做完第一条 product 测试后再继续讨论。

---

## 七、Git 操作备忘

```powershell
git add -A
git commit -m "描述做了什么"
git push
```

约定：**每个阶段至少一个独立 commit**。
git 历史本身就是你成长过程的证据，面试官会看提交记录，
一次性 push 完整项目反而可疑。

远端已配好（`origin` → pytest-api-automation.git），上游跟踪已建立，
`git push` 不需要再带参数。
