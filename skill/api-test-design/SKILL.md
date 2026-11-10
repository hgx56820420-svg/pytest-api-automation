---
name: api-test-design
description: 从需求文档设计并编写接口业务测试：提取需求规则 → 覆盖矩阵 → 测试点 → 测试用例（cases/<service>.yaml），人审后再写带 @pytest.mark.case 的 pytest。用户说"根据需求生成测试用例""设计接口测试""给某个服务补测试""需求变了更新用例"时使用。
---

# 接口测试设计

输入：需求文档（`docs/*.md` 或用户给的文本）、接口基线 `contracts/<service>.openapi.json`。
产出：`cases/<service>.yaml`，人审通过后再写 `tests/<service>/test_*.py`。

AI 负责起草，`scripts/check_cases.py` 负责校验，人负责审核。任何一步不确定，停下来问人，不要猜。

## 硬性规则

1. **只依据需求文档。** 不要读被测服务的源码，也不要先调接口看返回什么再写预期。那样测的是"代码现在怎么做"，不是"需求要求怎么做"，缺陷会被固化成预期。
2. **每条规则都要有原文引用。** `quote` 必须是需求文档里连续的一段原话（空白可以不同），校验脚本会逐字查找。
3. **需求没写清楚就提问。** 写进 `questions`，`status: open`，然后停下来把问题列给用户。用户回答后改成 `answered` 并填 `answer`，规则里引用答案。
4. **接口以契约为准。** `api` 写成 `METHOD /path`，必须和 `contracts/<service>.openapi.json` 里的路径一致。需求里提到契约中没有的接口，作为问题提出。

## 阶段 ① 提取需求规则

一条规则只描述一个可验证的行为，写清楚条件和结果。

```yaml
service: mini_shop
source: docs/MINI_SHOP_API_REQUIREMENTS.md
rules:
  - id: R-ORDER-03
    text: 库存不足时下单返回 400，库存、余额和订单数不变
    quote: "失败不变量：任意前置条件失败时，库存、余额和订单总数均不得变化。"
    api: POST /api/orders
questions:
  - id: Q-01
    text: 已支付订单能否取消退款？
    status: open
```

编号规则：`R-<模块>-<序号>`、`Q-<序号>`。

### 必问清单

需求文档最容易漏的是"没写"的规则。提取完规则后，对每类资源（订单、账户、商品……）逐项检查下面这张清单。需求里有答案的，写成规则；没有答案的，写成问题。**不能因为需求没提就跳过。**

| 主题 | 要回答的问题 |
|---|---|
| 归属 | 谁能看、谁能改？别人的资源返回 403 还是 404？管理员有没有例外？ |
| 状态终态 | 哪些状态是终态？终态上的每个操作返回什么？ |
| 幂等与重复 | 同一请求重复提交（重复支付、重复取消、重复注册）结果是什么？ |
| 并发 | 多个请求同时改同一资源时，哪个数不能超（库存、余额、次数）？ |
| 金额与数值 | 精度几位、怎么舍入？0、负数、极大值允许吗？ |
| 时间 | 时区怎么处理？过去的时间、跨天、时长上限？ |
| 级联影响 | 一个资源变化（下架、停用、改价、删除）对已有关联数据（订单、购物车、预约）有什么影响？ |
| 审计与可追溯 | 哪些操作必须留记录？记录里关联什么？ |

问题写成可以直接回答的形式，例如"0.1 × 3 的订单金额是 0.3 还是 0.30000000000000004？"，不要写"金额精度需确认"。

## 阶段 ② 覆盖矩阵

每条规则逐个过七个维度，每格只能是 `test` 或 `"n/a: 理由"`：

| 维度 | 问自己 |
|---|---|
| `happy` | 正常输入时结果对不对 |
| `boundary` | 数值、长度、数量的边界（0、1、上限、上限+1） |
| `invalid_input` | 缺字段、类型错、格式错是否返回 422 / 400 |
| `permission` | 未登录、普通用户、别人的资源 |
| `state` | 状态机：哪些状态允许、哪些返回 409，重复操作 |
| `side_effect` | 数据库是否按规则变化；失败时是否完全不变 |
| `concurrency` | 多个请求同时操作同一资源：超卖、重复扣款、重复使用 |

```yaml
coverage:
  R-ORDER-03:
    happy: "n/a: 正常下单由 R-ORDER-01 覆盖"
    boundary: test
    invalid_input: "n/a: 请求体校验由 R-ORDER-06 覆盖"
    permission: "n/a: 与权限无关"
    state: "n/a: 不涉及订单状态"
    side_effect: test
    concurrency: test
```

"n/a" 的理由要具体，不能写"不需要"。

## 阶段 ③ 测试点

覆盖矩阵里每个 `test` 格子至少一个测试点，测试点的 `rule` 和 `dimension` 必须对应那一格。

```yaml
test_points:
  - id: TP-ORDER-03-01
    rule: R-ORDER-03
    dimension: boundary
    text: 购买数量等于库存时成功，库存加一时失败
```

**到这里先停下**：运行校验脚本，把测试点清单发给用户审。测试点比用例短，审核成本最低，需求理解错误在这一步拦住。用户确认后再进入阶段 ④。

## 阶段 ④ 测试用例

每个测试点至少一条用例。`expected` 写成可以直接断言的结果（状态码、字段值、数据库变化）。

```yaml
cases:
  - id: TC-ORDER-003
    test_points: [TP-ORDER-03-01]
    title: 购买数量超过库存
    steps:
      - 管理员创建库存为 1 的商品
      - 买家购买 2 件
    expected: 返回 400 Insufficient stock；库存、余额、订单数不变
    testable: api
```

`testable` 取值：
- `api`：接口能测，下一步写 pytest。
- `ui`：需要页面操作，填 `reason`，交给 UI 测试。
- `manual`：需要人工判断或外部条件，填 `reason`。

出现下面三种情况，不要标成 ui / manual 了事，要提问停下：需要的接口在契约里不存在、预期结果需求里没写、缺少测试数据或账号。

## 校验

```powershell
.\.venv\Scripts\python scripts/check_cases.py cases/<service>.yaml
```

有 `ERROR` 必须修完才能继续。`WARN ... has no pytest yet` 表示接口用例还没写测试，是下一步的工作清单。

## 写 pytest

每条 `testable: api` 的用例写一个测试，放在 `tests/<service>/`，用插件提供的 fixture：

```python
@pytest.mark.case("TC-ORDER-003")
def test_buy_more_than_stock_changes_nothing(api, db, buyer, create_product):
    product = create_product(stock=1)
    before = (db.row("products", product["id"])["stock"], db.count("orders", user_id=buyer.id))

    response = api.post("/api/orders", user=buyer, json={"product_id": product["id"], "quantity": 2})

    assert response.status_code == 400
    assert (db.row("products", product["id"])["stock"], db.count("orders", user_id=buyer.id)) == before
```

- 每条断言都要能对应到用例的 `expected`，再对应到规则。
- 失败路径必须断言数据库没有变化，只断言状态码不够。
- 用 `api.register()` 造随机用户，不写死 id、用户名、密码。
- 并发用例用 `concurrent.futures.ThreadPoolExecutor` 同时发请求，断言成功次数和最终数据。

写完后运行 `.\.venv\Scripts\python scripts/check_tests.py`，它会拦住：没有断言；`assert True` 这类常量断言；`try/except` 吞掉异常；没理由的 `skip`；缺 `@pytest.mark.case`；用例属于 side_effect / state / concurrency 维度但测试没查数据库。另外自查一条脚本查不出来的：断言的预期值是从需求推出来的，不是先跑一遍接口照抄的。

最后运行 `pytest tests/<service>`。插件会按 `contracts/` 里的基线校验每个响应，契约外的状态码或字段会直接让测试失败。测试失败时先看报告里的 `api evidence`，判断是服务缺陷还是测试写错；**服务缺陷不要改测试去迁就它**，报告给用户。
