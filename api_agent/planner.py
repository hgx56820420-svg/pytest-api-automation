"""V1 deterministic case planner and coverage reviewer."""

from __future__ import annotations

from api_agent.models import (
    CoverageItem,
    CoverageReport,
    NormalizedRequirement,
    TestCase,
    TestCaseDocument,
    RequirementReview,
)


SCENARIOS: dict[str, tuple[str, str, list[str], list[str]]] = {
    "health_health_get": ("health_check", "contract", ["http_status", "response_schema"], ["http"]),
    "register_api_auth_register_post": ("register", "contract", ["http_status", "response_schema"], ["http"]),
    "login_api_auth_login_post": ("login", "security", ["http_status", "token_present"], ["http"]),
    "me_api_auth_me_get": ("current_user", "security", ["http_status", "authenticated_user"], ["http"]),
    "list_products_api_products_get": ("list_products", "contract", ["http_status", "response_schema"], ["http"]),
    "create_product_api_products_post": ("create_product", "business", ["http_status", "product_created"], ["http", "database"]),
    "get_product_api_products__product_id__get": ("get_product", "contract", ["http_status", "response_schema"], ["http"]),
    "update_product_api_products__product_id__put": ("update_product", "business", ["http_status", "product_updated"], ["http", "database"]),
    "offline_product_api_products__product_id__delete": ("offline_product", "business", ["http_status", "product_off_sale"], ["http", "database"]),
    "create_order_api_orders_post": ("create_order", "business", ["http_status", "order_created", "inventory_decreased", "balance_decreased"], ["http", "database"]),
    "list_my_orders_api_orders_get": ("list_orders", "contract", ["http_status", "order_in_list"], ["http"]),
    "get_order_api_orders__order_id__get": ("get_order", "contract", ["http_status", "order_matches"], ["http"]),
    "pay_order_api_orders__order_id__pay_post": ("pay_order", "business", ["http_status", "order_status_paid"], ["http", "database"]),
    "cancel_order_api_orders__order_id__cancel_post": ("cancel_order", "business", ["http_status", "order_status_cancelled", "inventory_restored", "balance_restored"], ["http", "database"]),
    "get_cart_api_cart_get": ("get_cart", "business", ["http_status", "cart_user_isolation"], ["http", "database"]),
    "clear_cart_api_cart_delete": ("clear_cart", "business", ["http_status", "cart_cleared"], ["http", "database"]),
    "add_cart_item_api_cart_items_post": ("add_cart_item", "business", ["http_status", "cart_item_added", "stock_limit"], ["http", "database"]),
    "update_cart_item_api_cart_items__product_id__put": ("update_cart_item", "business", ["http_status", "cart_quantity_updated"], ["http", "database"]),
    "remove_cart_item_api_cart_items__product_id__delete": ("remove_cart_item", "business", ["http_status", "cart_item_removed"], ["http", "database"]),
    "list_coupons_api_coupons_get": ("list_coupons", "contract", ["http_status", "response_schema"], ["http"]),
    "create_coupon_api_coupons_post": ("create_coupon", "business", ["http_status", "coupon_created"], ["http", "database"]),
    "transactions_api_inventory__product_id__transactions_get": ("inventory_transactions", "business", ["http_status", "inventory_evidence"], ["http", "database"]),
}

EXTRA_CASES = [
    TestCase(
        case_id="auth.register.boundaries", operation_id="register_api_auth_register_post", title="注册用户名边界", category="negative", scenario="register_boundaries", source_refs=["knowledge:review-coverage-script-001"], expected_status_codes=[201, 422], required_assertions=["boundary_statuses", "invalid_not_created"], evidence_requirements=["http", "database"],
    ),
    TestCase(
        case_id="auth.register.duplicate.rejected", operation_id="register_api_auth_register_post", title="重复用户名被拒绝", category="negative", scenario="duplicate_register", source_refs=["knowledge:review-coverage-script-001"], expected_status_codes=[409], required_assertions=["http_status", "duplicate_not_created"], evidence_requirements=["http", "database"],
    ),
    TestCase(
        case_id="auth.login.wrong_password.rejected", operation_id="login_api_auth_login_post", title="错误密码登录被拒绝", category="negative", scenario="wrong_password", source_refs=["knowledge:review-coverage-script-001"], expected_status_codes=[401], required_assertions=["http_status", "error_detail"], evidence_requirements=["http"],
    ),
    TestCase(
        case_id="auth.me.missing_token.rejected", operation_id="me_api_auth_me_get", title="未携带 Token 访问用户信息", category="negative", scenario="missing_token", source_refs=["knowledge:review-coverage-script-001"], expected_status_codes=[401], required_assertions=["http_status", "error_detail"], evidence_requirements=["http"],
    ),
    TestCase(
        case_id="products.create.missing_token.rejected", operation_id="create_product_api_products_post", title="未认证不能创建商品", category="negative", scenario="create_product_missing_token", source_refs=["knowledge:review-coverage-script-001"], expected_status_codes=[401], required_assertions=["http_status", "product_not_created"], evidence_requirements=["http", "database"],
    ),
    TestCase(
        case_id="products.get.not_found", operation_id="get_product_api_products__product_id__get", title="查询不存在商品", category="negative", scenario="get_product_not_found", source_refs=["knowledge:review-coverage-script-001"], expected_status_codes=[404], required_assertions=["http_status", "error_detail"], evidence_requirements=["http"],
    ),
    TestCase(
        case_id="products.create.invalid_price", operation_id="create_product_api_products_post", title="商品价格非法", category="negative", scenario="create_product_invalid_price", source_refs=["knowledge:review-coverage-script-001"], expected_status_codes=[422], required_assertions=["http_status", "product_not_created"], evidence_requirements=["http", "database"],
    ),
    TestCase(
        case_id="products.list.invalid_pagination", operation_id="list_products_api_products_get", title="商品分页参数非法", category="negative", scenario="list_products_invalid_pagination", source_refs=["knowledge:review-coverage-script-001"], expected_status_codes=[422], required_assertions=["pagination_boundaries"], evidence_requirements=["http"],
    ),
    TestCase(
        case_id="orders.create.product_not_found", operation_id="create_order_api_orders_post", title="商品不存在时不能下单", category="negative", scenario="order_product_not_found", source_refs=["knowledge:business-order-invariants-001"], expected_status_codes=[404], required_assertions=["http_status", "order_not_created"], evidence_requirements=["http", "database"],
    ),
    TestCase(
        case_id="orders.create.invalid_quantity", operation_id="create_order_api_orders_post", title="订单数量非法", category="negative", scenario="order_invalid_quantity", source_refs=["knowledge:business-order-invariants-001"], expected_status_codes=[422], required_assertions=["http_status", "order_not_created"], evidence_requirements=["http", "database"],
    ),
    TestCase(
        case_id="orders.cancel.paid.rejected", operation_id="cancel_order_api_orders__order_id__cancel_post", title="已支付订单不能取消", category="negative", scenario="cancel_paid", source_refs=["knowledge:business-order-invariants-001"], expected_status_codes=[409], required_assertions=["http_status", "state_unchanged"], evidence_requirements=["http", "database"],
    ),
    TestCase(
        case_id="orders.cancel.cancelled.rejected", operation_id="cancel_order_api_orders__order_id__cancel_post", title="已取消订单不能重复取消", category="negative", scenario="cancel_cancelled", source_refs=["knowledge:business-order-invariants-001"], expected_status_codes=[409], required_assertions=["http_status", "state_unchanged"], evidence_requirements=["http", "database"],
    ),
    TestCase(
        case_id="orders.get.other_user.hidden", operation_id="get_order_api_orders__order_id__get", title="用户不能访问他人订单", category="security", scenario="other_user_order", source_refs=["knowledge:review-coverage-script-001"], expected_status_codes=[404], required_assertions=["http_status", "order_hidden"], evidence_requirements=["http"],
    ),
    TestCase(
        case_id="orders.create.insufficient_stock.no_side_effect",
        operation_id="create_order_api_orders_post",
        title="库存不足时不得产生数据库副作用",
        category="negative",
        scenario="insufficient_stock",
        source_refs=["knowledge:business-order-invariants-001"],
        expected_status_codes=[400],
        required_assertions=["http_status", "inventory_unchanged", "balance_unchanged", "order_not_created"],
        evidence_requirements=["http", "database"],
    ),
    TestCase(
        case_id="orders.create.insufficient_balance.no_side_effect",
        operation_id="create_order_api_orders_post",
        title="余额不足时不得产生数据库副作用",
        category="negative",
        scenario="insufficient_balance",
        source_refs=["knowledge:business-order-invariants-001"],
        expected_status_codes=[400],
        required_assertions=["http_status", "inventory_unchanged", "balance_unchanged", "order_not_created"],
        evidence_requirements=["http", "database"],
    ),
    TestCase(
        case_id="orders.pay.twice.rejected",
        operation_id="pay_order_api_orders__order_id__pay_post",
        title="订单不能重复支付",
        category="negative",
        scenario="pay_twice",
        source_refs=["knowledge:business-order-invariants-001"],
        expected_status_codes=[409],
        required_assertions=["http_status", "order_status_paid"],
        evidence_requirements=["http", "database"],
    ),
]


def plan_cases(requirement: NormalizedRequirement, review: RequirementReview | None = None) -> TestCaseDocument:
    cases: list[TestCase] = []
    operation_ids = {operation.operation_id for operation in requirement.operations}
    for operation in requirement.operations:
        scenario = SCENARIOS.get(operation.operation_id)
        if scenario is None:
            continue
        scenario_name, category, assertions, evidence = scenario
        documented_success = [int(code) for code in operation.responses if code.isdigit() and 200 <= int(code) < 300]
        source_refs = [operation.source_ref]
        if review and operation.operation_id in review.operation_mapping:
            source_refs.append(f"requirement:{review.operation_mapping[operation.operation_id]}")
        cases.append(
            TestCase(
                case_id=f"operation.{operation.operation_id}",
                operation_id=operation.operation_id,
                title=operation.summary or operation.operation_id,
                category=category,
                scenario=scenario_name,
                source_refs=source_refs,
                expected_status_codes=documented_success or [200],
                required_assertions=assertions,
                evidence_requirements=evidence,
            )
        )
    for template in EXTRA_CASES:
        if template.operation_id not in operation_ids:
            continue
        case = template.model_copy(deep=True)
        if review and case.operation_id in review.operation_mapping:
            case.source_refs.append(f"requirement:{review.operation_mapping[case.operation_id]}")
        cases.append(case)
    return TestCaseDocument(requirement_hash=requirement.source_hash, cases=cases)


def review_coverage(
    requirement: NormalizedRequirement,
    document: TestCaseDocument,
    requirement_review: RequirementReview | None = None,
) -> CoverageReport:
    items: list[CoverageItem] = []
    issues: list[str] = []
    by_operation: dict[str, list[TestCase]] = {}
    for case in document.cases:
        by_operation.setdefault(case.operation_id, []).append(case)

    for operation in requirement.operations:
        cases = by_operation.get(operation.operation_id, [])
        if not cases:
            status = "unsupported" if operation.operation_id not in SCENARIOS else "missing"
            issues.append(f"No executable V1 case for {operation.operation_id}")
            items.append(CoverageItem(operation_id=operation.operation_id, case_ids=[], status=status))
            continue
        missing = [
            assertion
            for assertion in ("http_status",)
            if not any(assertion in case.required_assertions for case in cases)
        ]
        items.append(
            CoverageItem(
                operation_id=operation.operation_id,
                case_ids=[case.case_id for case in cases],
                status="covered" if not missing else "missing",
                missing_assertions=missing,
            )
        )
        if missing:
            issues.append(f"{operation.operation_id} is missing assertions: {', '.join(missing)}")

    if requirement_review and requirement_review.detected_scenarios:
        generated_scenarios = {case.scenario for case in document.cases}
        missing_scenarios = sorted(set(requirement_review.detected_scenarios) - generated_scenarios)
        for scenario in missing_scenarios:
            issues.append(f"Requirement scenario is not generated: {scenario}")

    covered = sum(item.status == "covered" for item in items)
    business_assertions = sum(
        len(case.required_assertions) for case in document.cases if case.category in {"business", "negative"}
    )
    return CoverageReport(
        decision="approved" if covered == len(requirement.operations) and not issues else "needs_revision",
        operations_total=len(requirement.operations),
        operations_covered=covered,
        cases_total=len(document.cases),
        business_assertions_total=business_assertions,
        items=items,
        issues=issues,
    )
