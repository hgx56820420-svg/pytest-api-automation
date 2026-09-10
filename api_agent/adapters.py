"""Domain adapter registry: make the V2 pipeline service-agnostic.

An adapter binds one tested service to the framework:

- ``scenarios``: operation_id -> (scenario name, category, assertions, evidence)
- ``extra_cases``: negative/business case templates beyond the main cases
- ``executor_class``: the scenario executor used by the generated pytest
- ``observer_class``: read-only database observer for side-effect evidence
- ``blanket_auth``: section-level auth rules for the requirement parser

Adapters carry no execution logic themselves; they only register what the
deterministic tools need to drive a specific service.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from api_agent.database import DatabaseObserver
from api_agent.executor import ScenarioExecutor
from api_agent.library_executor import LibraryExecutor
from api_agent.library_observer import LibraryObserver
from api_agent.models import TestCase


@dataclass
class DomainAdapter:
    name: str
    scenarios: dict[str, tuple[str, str, list[str], list[str]]]
    extra_cases: list[TestCase]
    executor_class: type
    observer_class: type
    blanket_auth: list[tuple[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# mini_shop adapter (data moved here from planner.py)
# ---------------------------------------------------------------------------

MINI_SHOP_SCENARIOS: dict[str, tuple[str, str, list[str], list[str]]] = {
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

MINI_SHOP_EXTRA_CASES = [
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

# ---------------------------------------------------------------------------
# library adapter (second tested service, used to validate framework generics)
# ---------------------------------------------------------------------------

LIBRARY_SCENARIOS: dict[str, tuple[str, str, list[str], list[str]]] = {
    "health_health_get": ("health_check", "contract", ["http_status", "response_schema"], ["http"]),
    "register_api_auth_register_post": ("register", "contract", ["http_status", "response_schema"], ["http"]),
    "login_api_auth_login_post": ("login", "security", ["http_status", "token_present"], ["http"]),
    "me_api_auth_me_get": ("current_user", "security", ["http_status", "authenticated_user"], ["http"]),
    "list_books_api_books_get": ("list_books", "contract", ["http_status", "response_schema"], ["http"]),
    "create_book_api_books_post": ("create_book", "business", ["http_status", "book_created", "book_count_increased"], ["http", "database"]),
    "get_book_api_books__book_id__get": ("get_book", "contract", ["http_status", "response_schema"], ["http"]),
    "update_book_api_books__book_id__put": ("update_book", "business", ["http_status", "book_updated"], ["http", "database"]),
    "offline_book_api_books__book_id__delete": ("offline_book", "business", ["http_status", "book_off_shelf"], ["http", "database"]),
    "create_borrow_api_borrows_post": ("create_borrow", "business", ["http_status", "borrow_created", "copies_decreased", "deposit_decreased"], ["http", "database"]),
    "list_borrows_api_borrows_get": ("list_borrows", "contract", ["http_status", "borrow_in_list"], ["http"]),
    "return_borrow_api_borrows__borrow_id__return_post": ("return_borrow", "business", ["http_status", "borrow_status_returned", "copies_restored", "deposit_restored"], ["http", "database"]),
}

LIBRARY_EXTRA_CASES = [
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
        case_id="books.create.missing_token.rejected", operation_id="create_book_api_books_post", title="未认证不能创建图书", category="negative", scenario="create_book_missing_token", source_refs=["knowledge:review-coverage-script-001"], expected_status_codes=[401], required_assertions=["http_status", "book_not_created"], evidence_requirements=["http", "database"],
    ),
    TestCase(
        case_id="books.get.not_found", operation_id="get_book_api_books__book_id__get", title="查询不存在图书", category="negative", scenario="get_book_not_found", source_refs=["knowledge:review-coverage-script-001"], expected_status_codes=[404], required_assertions=["http_status", "error_detail"], evidence_requirements=["http"],
    ),
    TestCase(
        case_id="books.list.invalid_pagination", operation_id="list_books_api_books_get", title="图书分页参数非法", category="negative", scenario="list_books_invalid_pagination", source_refs=["knowledge:review-coverage-script-001"], expected_status_codes=[422], required_assertions=["pagination_boundaries"], evidence_requirements=["http"],
    ),
    TestCase(
        case_id="borrows.create.book_not_found", operation_id="create_borrow_api_borrows_post", title="图书不存在时不能借阅", category="negative", scenario="borrow_book_not_found", source_refs=["knowledge:business-order-invariants-001"], expected_status_codes=[404], required_assertions=["http_status", "borrow_not_created"], evidence_requirements=["http", "database"],
    ),
    TestCase(
        case_id="borrows.create.off_shelf.rejected", operation_id="create_borrow_api_borrows_post", title="下架图书不能借阅", category="negative", scenario="borrow_off_shelf", source_refs=["knowledge:business-order-invariants-001"], expected_status_codes=[400], required_assertions=["http_status", "copies_unchanged", "deposit_unchanged", "borrow_not_created"], evidence_requirements=["http", "database"],
    ),
    TestCase(
        case_id="borrows.create.insufficient_copies.no_side_effect", operation_id="create_borrow_api_borrows_post", title="副本不足时不得产生数据库副作用", category="negative", scenario="insufficient_copies", source_refs=["knowledge:business-order-invariants-001"], expected_status_codes=[400], required_assertions=["http_status", "copies_unchanged", "deposit_unchanged", "borrow_not_created"], evidence_requirements=["http", "database"],
    ),
    TestCase(
        case_id="borrows.create.insufficient_deposit.no_side_effect", operation_id="create_borrow_api_borrows_post", title="押金余额不足时不得产生数据库副作用", category="negative", scenario="insufficient_deposit", source_refs=["knowledge:business-order-invariants-001"], expected_status_codes=[400], required_assertions=["http_status", "copies_unchanged", "deposit_unchanged", "borrow_not_created"], evidence_requirements=["http", "database"],
    ),
    TestCase(
        case_id="borrows.create.missing_token.rejected", operation_id="create_borrow_api_borrows_post", title="未认证不能借书", category="negative", scenario="borrow_missing_token", source_refs=["knowledge:review-coverage-script-001"], expected_status_codes=[401], required_assertions=["http_status", "copies_unchanged", "borrow_not_created"], evidence_requirements=["http", "database"],
    ),
    TestCase(
        case_id="borrows.return.twice.rejected", operation_id="return_borrow_api_borrows__borrow_id__return_post", title="已归还借阅不能重复归还", category="negative", scenario="return_twice", source_refs=["knowledge:business-order-invariants-001"], expected_status_codes=[409], required_assertions=["http_status", "state_unchanged"], evidence_requirements=["http", "database"],
    ),
    TestCase(
        case_id="borrows.return.other_user.hidden", operation_id="return_borrow_api_borrows__borrow_id__return_post", title="不能归还他人借阅记录", category="security", scenario="return_other_user", source_refs=["knowledge:review-coverage-script-001"], expected_status_codes=[404], required_assertions=["http_status", "borrow_hidden", "state_unchanged"], evidence_requirements=["http", "database"],
    ),
]

MINI_SHOP_ADAPTER = DomainAdapter(
    name="mini_shop",
    scenarios=MINI_SHOP_SCENARIOS,
    extra_cases=MINI_SHOP_EXTRA_CASES,
    executor_class=ScenarioExecutor,
    observer_class=DatabaseObserver,
    blanket_auth=[
        ("所有订单接口均需要认证", "/api/orders"),
        ("所有购物车接口都需要 Bearer Token", "/api/cart"),
    ],
)

LIBRARY_ADAPTER = DomainAdapter(
    name="library",
    scenarios=LIBRARY_SCENARIOS,
    extra_cases=LIBRARY_EXTRA_CASES,
    executor_class=LibraryExecutor,
    observer_class=LibraryObserver,
    blanket_auth=[
        ("所有借阅接口均需要认证", "/api/borrows"),
    ],
)

ADAPTERS: dict[str, DomainAdapter] = {
    MINI_SHOP_ADAPTER.name: MINI_SHOP_ADAPTER,
    LIBRARY_ADAPTER.name: LIBRARY_ADAPTER,
}

DEFAULT_ADAPTER = "mini_shop"


def get_adapter(name: str | None = None) -> DomainAdapter:
    resolved = name or DEFAULT_ADAPTER
    if resolved not in ADAPTERS:
        raise ValueError(f"Unknown adapter: {resolved}; available: {', '.join(sorted(ADAPTERS))}")
    return ADAPTERS[resolved]


def create_executor(
    adapter_name: str | None,
    *,
    base_url: str,
    database_url: str,
    evidence_dir: Any,
    run_id: str,
    requirement: Any,
):
    """Build the adapter's executor; called from the generated pytest."""
    adapter = get_adapter(adapter_name)
    return adapter.executor_class(
        base_url=base_url,
        database_url=database_url,
        evidence_dir=evidence_dir,
        run_id=run_id,
        requirement=requirement,
    )
