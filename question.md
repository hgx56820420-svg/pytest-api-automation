# 学习笔记 — 踩坑与知识点整理

> 记录到：阶段 4（数据驱动 + 响应结构校验）进行中
> 用途：复盘用，也是面试前快速过一遍"我做过什么、踩过什么坑"的材料

---

## 阶段 1：搭建被测服务（FastAPI，由 AI 编写，我负责读懂）

阶段 1 的产物由 AI 写，不是我自己动手写代码，所以这里记的是"我需要理解的知识点"，不是"我踩过的坑"。

### 知识点

**FastAPI 在这个项目里的角色**：负责定义接口路径、请求方式（GET/POST/...）、接收请求并调用业务逻辑。跟 uvicorn / Pydantic / SQLAlchemy 的关系：

- **uvicorn**：真正启动、运行 FastAPI 应用，监听端口。FastAPI 只是应用代码，本身不会自己"跑起来"。
- **Pydantic**：定义请求体、响应体的数据契约，自动做参数校验（不合法直接返回 422），也是 `app/schemas.py` 的核心。
- **SQLAlchemy**：让 Python 操作数据库（这里是 SQLite），对应 `app/models.py` 的三张表 User/Product/Order。

**接口契约是什么**：调用方和服务方之间"这个接口怎么用"的约定——包括 URL、方法、请求字段及其类型/取值范围、响应结构、各种情况下的状态码。契约不是猜出来的，是从 `app/schemas.py` 里的 Pydantic 模型直接读出来的，比如：

```python
class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=20)
    password: str = Field(min_length=6, max_length=32)
```

这段代码本身就是测试用例的来源——阶段 4 做的 username 长度边界值参数化（2/3/20/21），就是直接照着这两个数字设计的。

**服务里刻意埋的测试点**（来自 `HANDOFF.md`，指导后续所有阶段的用例设计方向）：
- 鉴权链路：无 token / 格式错 / 签名错 / 过期 / 用户已删 → 全部 401
- 库存边界：stock=1 时下单 2 个 → 400
- 状态机非法流转：已支付订单再取消 → 409
- 参数校验：page=0、size=-1、quantity=0 → 422
- 越权访问：用 A 的 token 查 B 的订单 → 404（不是 403，避免暴露资源是否存在）
- 副作用与回滚：取消订单后要断言库存和余额真的回到了原值，不能只看响应体的 status 字段

### 环境搭建阶段的坑（不是代码逻辑坑，是环境配置坑）

| 现象 | 原因 | 解决 |
|---|---|---|
| `git push` 报 `src refspec main does not match any` | 本地仓库还没有任何 commit，`main` 是"未出生分支"，没有内容可推 | 先 `git commit`（哪怕是空提交），有了 commit 之后 `main` 才真正存在 |
| `git remote add` 报 `is not valid remote name` | 把 URL 当成了第一个参数（远端名字），正确顺序是 `git remote add origin <URL>` | 记住第一个参数是名字（约定俗成 `origin`），第二个才是地址 |
| PowerShell 里 `git add -A && git commit ...` 报错 | PowerShell 5.1 不支持 `&&` 语法 | 用 `;` 分隔多条命令 |
| pip 装 `pydantic==2.11.7` 失败，报连接超时/编译错误 | 本机 Python 3.14 太新，这个版本的 `pydantic-core` 没有预编译轮子，pip 会尝试下载 Rust 工具链源码编译 | 升级 `pydantic` 到 `2.13.4`（有 cp314 预编译包），不要用旧版本锁定 |
| uvicorn 启动报 `[Errno 10048]` 端口已被占用 | 8000 端口被其他程序占用，之前启动的服务实例也没关掉 | 统一约定用 8010 端口；同一端口不要反复起多个实例 |

### 方法论

**从接口契约推导测试用例，而不是从需求文档猜**——契约里写了 `min_length=3`，就直接测 2/3/20/21 这四个边界值，不用凭感觉编数据。这是面试被问"你怎么设计测试用例"时最专业的答案。

---

## 阶段 2：裸写测试（requests + pytest，无封装）

### 知识点

**pytest 怎么发现测试**：函数名以 `test_` 开头，pytest 自动收集执行,不需要注册或声明。

**为什么每条用例要动态生成 username**：用固定字符串会导致重复运行时命中"用户名已存在"（409），而不是你测试意图里的场景。用 `uuid.uuid4().hex[:8]` 保证每次运行都是新值。

### 踩过的坑

| 现象 | 原因 | 教训 |
|---|---|---|
| pytest 显示 `collected 0 items` / `no tests ran` | 改了代码但没保存文件，pytest 读的是硬盘上的旧内容 | 开 VS Code 自动保存（`files.autoSave` → `afterDelay`），从根源避免 |
| 缩进错位，函数体"掉出"函数外变成模块顶层代码 | 复制代码时手动调整缩进出错 | 缩进在 Python 里是语法的一部分，错一个空格代码语义就变了 |
| 写了两个同名函数 `test_register_success` | 后面的定义会静默覆盖前面的，前一个测试等于没写 | Python 不会对重复定义报错，要靠自己检查文件里有没有重名函数 |
| `test_login_wrong_password` 断言失败，实际返回 200 | 注册和登录复用了同一个 `request_body`，密码根本没变过，"密码错误"这个场景根本没被构造出来 | **测试意图变了，输入数据也要跟着变**；不能图省事复用同一个变量当两种不同场景的输入 |

---

## 阶段 3：fixture 复用 + 多环境配置

### 知识点

**`@pytest.fixture` 怎么注入**：不是靠 import，是靠**测试函数的参数名**匹配。pytest 看到参数名 `base_url`，会自动去找同名的 fixture 函数执行并把返回值传进来。

**fixture 可以互相依赖**：`registered_user(base_url)` 这种写法，pytest 会先执行 `base_url`，再把结果传给 `registered_user`。

**环境变量的本质**：
- `os.getenv("KEY", "默认值")`——查操作系统环境变量表，查不到就用默认值
- 环境变量表存在于**进程内存里**，不是文件；子进程会**继承**父进程当时的那份表
- 关掉终端窗口，进程被系统回收，这份内存也就没了——不是"被删除"，是"进程不在了"
- 这也是为什么 CI（GitHub Actions 等）能在不碰代码的情况下切换测试目标：CI 的运行环境（父进程）设置好变量,再启动 pytest（子进程），子进程自动继承

**为什么密码不能写进代码里**：git 历史会永久保留提交记录，删了也能从历史里翻出来。要用环境变量或 CI 的 Secrets 功能，代码里只写 `os.getenv(...)`，真正的值不进版本库。

**CI/CD 是什么**：
- CI（持续集成）：每次推代码自动跑测试/检查，尽早发现问题
- CD（持续部署）：测试通过后自动部署，不用人工操作
- GitHub Actions 是 GitHub 提供的 CI/CD 工具，配置文件放在 `.github/workflows/`

### 踩过的坑

| 现象 | 原因 | 教训 |
|---|---|---|
| `from conftest import base_url` | fixture 不需要 import，靠参数名注入；手动 import 虽然不报错但语义混乱 | fixture 的调用方式和普通函数不一样，别用旧习惯套 |
| `test_login_success` 里发了 `/register` 而不是 `/login`，返回 409 | `registered_user` fixture 已经注册过一次了，测试体里又手写了一次注册请求，用同一个用户名冲突 | 用了 fixture 之后要清楚"fixture 已经帮你做了什么"，不要重复做 |
| 类似地，`test_login_wrong_password` 也多写了一次注册 | 同上，`registered_user` 已经保证账号存在 | 一条测试应该只做它自己意图相关的动作，其余交给 fixture |
| 不知道怎么"改密码但不改用户名" | 需要基于 `registered_user` 返回的字典，造一个新字典，只覆盖 password 字段 | 手动写法：`{"username": x["username"], "password": "错的"}`；进阶写法：`{**x, "password": "错的"}`（字典展开+覆盖） |
| 改完 `conftest.py` 忘了导入 `os` | 加了 `os.getenv(...)` 但没在文件顶部 `import os` | 引入新函数记得检查对应的 import |

### 做过的验证（不只是让测试通过，而是证明配置真的生效）

1. 不设置环境变量直接跑 → 走默认值 `127.0.0.1:8010`，5 条全过
2. 故意设置成一个没人监听的端口（`9999`）→ 连接失败报错，证明环境变量真的被读取了，不是摆设

---

## 阶段 4：数据驱动（parametrize）+ 响应结构校验（jsonschema）

### 知识点

**`@pytest.mark.parametrize` 怎么用**：

```python
@pytest.mark.parametrize("参数名1,参数名2", [
    (值1a, 值1b),
    (值2a, 值2b),
])
def test_xxx(参数名1, 参数名2):
    ...
```

会把这一条测试函数重复执行 N 次（N = 列表长度），每次用不同的一组值。pytest 报告里会显示成独立的用例，比如 `test_xxx[值1a-值1b]`。

**parametrize 只负责喂数据，断言逻辑要自己写对**：如果断言里写死了固定值（比如 `assert x == 201`），不管传进来什么参数都只会跟 201 比较，等于没用上参数化的意义。

**jsonschema 怎么校验响应结构**：

```python
from jsonschema import validate

SCHEMA = {
    "type": "object",
    "properties": {"字段名": {"type": "string"}},
    "required": ["字段名"],
}

validate(instance=response.json(), schema=SCHEMA)
```

校验通过什么都不发生；不通过抛 `ValidationError`，报错信息会精确指出哪个字段、期望什么类型、实际是什么值。比一个个手写 `assert isinstance(...)` 更清晰。

**为什么要做结构校验而不是只测状态码**：只断言状态码，接不住"接口字段类型被悄悄改掉"这类问题（比如 `access_token` 从字符串变成数字）。

### 踩过的坑

| 现象 | 原因 | 教训 |
|---|---|---|
| `(2, 422)` 和 `(21, 422)` 两组参数化数据失败 | 断言写死了 `assert response.status_code == 201`，没用参数化传进来的 `expected_status` | 恰好合法边界值 `(3, 201)`、`(20, 201)` 因为"凑巧"和写死的 201 一致而通过，掩盖了断言写错的问题 |
| 改对断言后，`(3, 201)` 和 `(20, 201)` 反而失败，报 409 | 用户名固定写成 `"u" * username_length`，是个死值；第一次跑注册成功，第二次跑同样的值就撞上"用户名已存在" | 需要长度精确等于目标值、同时内容又唯一的字符串：`f"u{uuid.uuid4().hex}"[:username_length]`——前面拼动态随机串再截断到指定长度 |
| `NameError: name 'validate' is not defined` | 把 `from jsonschema import validate` 当作"看起来多余"的重复导入删掉了 | `TOKEN_SCHEMA`（来自 `conftest.py`，本项目内部）和 `validate`（来自第三方库 `jsonschema`）是两个不同来源，各自需要独立的 import，不能因为都在开头一起出现就当成同类删减 |

### 做过的验证

- 把 `TOKEN_SCHEMA` 里 `access_token` 的类型故意改成 `integer`，跑测试，确认真的报出 `ValidationError`，并且报错信息精确指出实际值是那串 JWT 字符串——证明这份 schema 真的在起校验作用，不是摆设代码

---

## 通用方法论（贯穿几个阶段反复出现的原则）

1. **别只让测试通过，要验证"检测能力"本身有没有意义**——故意构造一个应该失败的场景，看它是否真的能失败。用过的例子：环境变量指向错误端口、schema 类型故意写错。
2. **测试要能重复执行，不依赖上一次运行留下的状态**——固定值的测试数据一跑就"用过一次"，第二次跑就会跟历史数据冲突。所有需要唯一性的字段都要动态生成。
3. **用了封装（fixture）之后要清楚它已经做了什么，不要重复做**——这是阶段 3 反复出现的坑，本质是对抽象层的信任和理解不够。
4. **报错信息要读完整，它通常已经把根因写出来了**——`AssertionError` 里带的 `response.text`、jsonschema 的 `Failed validating 'type' in schema[...]` 都是直接给出诊断线索的，不用瞎猜。
