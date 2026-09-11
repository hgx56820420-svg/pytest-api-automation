# 新被测对象接入问题反馈记录

> 记录每次向框架接入新被测对象时暴露的问题。目的：让下一次接入更快，并沉淀框架层的改进方向。
>
> 背景：项目从单一 Mini Shop 演进到三个被测对象（mini_shop / library / meeting），
> 每次"换一个领域真跑一遍"都是对框架通用性的一次压力测试。

## 概览

| 接入 | 提交 | 框架层改动 | E2E 结果 | 暴露问题数 |
|---|---|---|---|---|
| library（第 2 个对象） | `b471dd0` | **大**：adapter 机制本身落地，SCENARIOS/执行器/观察者从框架剥离；需求 ID 正则放宽；通配认证规则参数化 | 首跑 FAIL（2 用例）→ 修复后 PASS 26/26 | 4（2 框架 + 1 执行器 + 1 测试桩） |
| meeting（第 3 个对象） | `b8b5e64` | **零**：只新增 DomainAdapter + 服务 + 文档 | 首跑 FAIL（1 用例）→ 修复后 PASS 28/28 | 1（服务契约缺陷） |

接入成本明显递减：library 的主要工作是把"业务适配层"从框架里拆出来；meeting 接入时框架层一行未改。

---

## 一、library 接入（第 2 个被测对象）

### 1. 需求 ID 正则不支持多段前缀【框架层】

- **现象**：`parse_requirements` 解析出 0 个接口，需求审核报 "No REQ-* API headings were found"。
- **根因**：正则 `REQ-[A-Z]+-\d+` 不允许 ID 中间出现连字符段，library 使用的 `REQ-LIB-META-001` 无法匹配。
- **修复**：放宽为 `REQ-[A-Z0-9]+(?:-[A-Z0-9]+)*-\d+`，兼容 `REQ-AUTH-001` 与 `REQ-LIB-META-001`。
- **教训**：框架对输入格式的隐含假设（ID 只有一段前缀）在第二个领域立即失效。

### 2. 接口级认证声明缺失，通配认证规则必须可插拔【框架层】

- **现象**：library 文档的借阅接口没有逐接口写"认证：需要"，只写了段落级规则"所有借阅接口均需要认证"，解析出的 `auth_required` 全为 False。
- **修复**：把 `BLANKET_AUTH`（文本标记 → 路径前缀）改为由 adapter 提供，workflow 按当前 adapter 传入。
- **教训**：文档写作惯例因领域而异，解析器的"常识"必须随 adapter 走。

### 3. 执行器变量未定义（NameError）【服务适配层】

- **现象**：E2E 中 `operation.return_borrow_*` 失败，断言 `scenario_execution: NameError: name 'borrow' is not defined`。
- **根因**：`LibraryExecutor._scenario_return_borrow` 中引用了不存在的局部变量（应为 `created.json()["deposit_charged"]`）。
- **教训**：**场景执行器只能靠真实 HTTP + 真实数据库跑出来**——单测桩（stub run_pipeline）不经过场景 handler，这类错误单测永远发现不了。

### 4. 固定测试数据导致修复重跑假失败【框架级数据隔离缺陷，最有价值】

- **现象**：`auth.register.boundaries` 在 E2E 修复重跑时失败：`boundary_statuses` 期望 `[422, 201, 201, 422]` 实际出现 409。
- **根因**：边界用例用固定用户名（`u×20`、`u×21`）。第一次运行已注册成功，修复回路在同一数据库重跑时同名用户返回 409，合法分支的 201 断言失败。
- **修复**：用户名改为每次运行追加唯一后缀（保持边界语义：2 字符拒绝 / 3–20 合法 / 21 拒绝）。
- **教训**：Mini Shop 三个月没暴露此问题，纯粹是因为每次 E2E 都用全新数据库。**"用例必须可在同一环境重跑（rerun-safe）"是框架级约束，不是服务级问题**——受限修复回路放大了这一点：修复重跑是 V2 的常态路径。

### 5. 测试桩签名漂移【测试层，轻微】

- **现象**：`pipeline.run` 新增 `adapter_name` 参数后，测试 stub 签名未同步，workflow stub E2E 报 `TypeError`。
- **教训**：框架函数加参数时，测试桩与真实实现共用调用路径（workflow → pipeline），同步修改是隐性义务。

### 6. 需求文档措辞与场景标记的隐性耦合【文档写作约束，预防性发现】

- **现象**（链路验证阶段即确认）：mini shop 的场景检测（`SCENARIO_MARKERS`）是对文档全文做子串匹配。library 文档若出现"分页参数"、"余额不足"、"商品不存在"等词，会被误判为 mini shop 场景 → 覆盖审核报"Requirement scenario is not generated" → 工作流卡进 NEEDS_HUMAN。
- **应对**：写 library/meeting 文档时刻意规避这些词（如用"分页约束"替代"分页参数"、"押金不足"替代"余额不足"）。
- **教训**：这是目前跨领域接入**最大的隐性约束**——文档措辞必须避开其他领域的标记词。建议后续把标记检测也 adapter 化，或改成语义匹配。

---

## 二、meeting 接入（第 3 个被测对象）

### 1. 手动 422 与 OpenAPI 声明的响应结构不一致【服务契约缺陷，新验证维度】

- **现象**：`bookings.create.invalid_time_range` 失败，`response_schema` 断言：`'end_time must be after start_time' is not of type 'array'`。
- **根因**：服务用 `HTTPException(422, detail="字符串")` 手动抛 422，但 FastAPI 在 OpenAPI 中声明的 422 响应 `detail` 是校验错误**数组**（`loc/msg/type`）。响应与机器契约不符。
- **修复**：时间范围校验移入 `BookingCreate` 的 `model_validator(mode="after")`，由 Pydantic 返回标准 422 数组结构；需求文档同步调整前置条件顺序说明。
- **教训**：这是前两个对象都没触发的验证维度——**response_schema 断言能抓住"响应结构与契约声明不符"**。Mini Shop 的 422 全部来自 Pydantic 自动校验所以从未暴露。给服务端写"手动 422"是常见错误，框架的契约校验正好兜住。

### 2. 接入流程本身零障碍【正向反馈】

- 链路验证（解析/审核/规划/覆盖）一次通过：12 接口解析、28 用例、覆盖 12/12。
- 时间冲突新维度（重叠 409 + 相邻时段放行）只用 adapter 内的场景与断言表达，框架未感知"时间"概念。
- stub 工作流 E2E 一次通过；真实 E2E 唯一失败即上述契约缺陷，且工作流正确走完"修复 2 次 → NEEDS_HUMAN/FAIL"兜底路径，没有假 PASS。

---

## 三、规律总结

1. **真实 E2E 不可省略**：两次接入暴露的 3 个功能性缺陷（NameError、重跑隔离、422 结构）全部是单测桩无法覆盖的。单测绿 ≠ 能跑。
2. **接入成本递减验证了 adapter 抽象**：第 2 个对象逼出了框架改造（正则、通配认证、插件机制），第 3 个对象框架层零改动——抽象的回报从第三个使用者开始兑现。
3. **数据必须是 rerun-safe 的**：受限修复回路意味着"同一环境重跑"是常态路径，任何固定测试数据（用户名、资源名）都必须带唯一后缀。
4. **文档措辞耦合是当前最大技术债**：跨领域子串标记检测（`SCENARIO_MARKERS` / `BUSINESS_RULE_MARKERS`）要求文档作者记住所有领域的"禁忌词"，建议后续 adapter 化或语义化。
5. **契约校验在保护服务端**：response_schema 断言抓住的 422 结构问题是典型的"服务能跑但与契约不符"，这正是 md-first + OpenAPI 双源设计的价值。

## 四、下一次接入 Checklist

- [ ] 服务具备四要素：认证 + CRUD + 状态机 + 可断言的数据库副作用
- [ ] 需求文档用 `#### REQ-XX-NNN \`METHOD /path\`` 标题（多段前缀可用）
- [ ] 文档措辞避开既有领域的场景标记词（分页参数/余额不足/商品/订单/库存不足/重复支付/price > 0 等），或先扩充 adapter 的标记表
- [ ] 段落级认证规则写入 adapter 的 `blanket_auth`
- [ ] 场景执行器中所有测试数据带唯一后缀（rerun-safe）；或使用 `--fixed-accounts` 固定账号模式（默认唯一后缀），此时必须为 adapter 配置 `cleanup_spec`
- [ ] 422 类校验一律走 Pydantic validator，不要手动 `HTTPException(422)`
- [ ] 链路验证四步：parse → review → plan/coverage → stub workflow E2E
- [ ] 真实 E2E 至少跑两轮（第二轮回跑验证 rerun-safe）

## 五、固定账号模式（--fixed-accounts）与数据清理

针对"想用固定用户名 + 清理工具回收"的需求，框架提供可选模式（默认仍为唯一后缀，天然 rerun-safe）：

```text
固定用户名 = agent_ + 用例ID派生 + 注册序号（同一用例跨运行同名）
  → 注册遇 409（上次残留）
  → CleanupTool（确定性 Python，直连测试库，api_agent/cleanup.py）：
      ① 真实性约束：先 SELECT 后 DELETE，账号不存在时如实上报 account_not_found，
         绝不虚构删除成功，也绝不猜测账号 ID
      ② 前缀护栏：只清理 agent_ 开头的测试账号，其余账号拒绝触碰
      ③ 用户名下的业务子记录（adapter 的 cleanup_spec.children）与账号同事务删除
  → 删除成功 → 重试注册一次；仍失败 → 自动退回唯一后缀新账号继续运行
  → 全部清理动作写入 agent-log.jsonl（cleanup_account 事件，含真实 user_id）
```

防幻觉设计：传入清理工具的账号标识永远来自确定性代码（固定用户名生成规则 / 数据库真实查询），不经过任何模型输出；删除结果为结构化 JSON，可审计。已在 meeting 服务上双轮 E2E 验证：第二轮 19 次撞车全部正确清理并重试成功，28/28 PASS。
