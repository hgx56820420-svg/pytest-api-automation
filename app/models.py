"""ORM 数据模型：User / Product / Order。

这三张表对应 app/schemas.py 里的请求/响应契约，也是 tests/ 下所有断言的
"标准答案"来源——比如测 username 长度边界值，就是照着这里字段的约束推的。

ORM（Object-Relational Mapping，对象关系映射）：让你用写 Python 类的方式
描述数据库表结构，SQLAlchemy 负责把这些类翻译成真正的 SQL 语句（建表、
增删改查），你不需要手写 SQL。
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _now():
    """生成带时区信息的当前时间，作为各表 created_at 字段的默认值。

    用 timezone.utc 而不是本地时间，是为了避免"服务器和客户端在不同时区"
    时数据对不上的问题——这是后端开发的通用规范，不是本项目特有的。
    """
    return datetime.now(timezone.utc)


class User(Base):
    """用户表。对应 tests/test_auth.py 里注册/登录测试的操作对象。"""

    # __tablename__：告诉 SQLAlchemy 这个类对应数据库里哪张表
    __tablename__ = "users"

    # Mapped[int] / mapped_column(...)：SQLAlchemy 2.0 的写法，
    # Mapped[类型] 声明这个字段在 Python 里是什么类型，
    # mapped_column(...) 声明它在数据库里的列属性（类型、约束等）
    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # 主键，自增

    # unique=True：数据库层面强制用户名不能重复，这是 409（用户名已存在）
    # 报错真正的底层保障——即使代码逻辑漏检查，数据库也会拒绝重复插入
    # index=True：给这一列建索引，按用户名查询（登录时）会更快
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)

    # 注意：这里存的是密码哈希（pbkdf2 加密后的结果），不是原始密码。
    # 这是安全基本要求——数据库泄露也不能直接拿到用户密码。
    # 加密逻辑在 app/auth.py 的 hash_password / verify_password。
    password_hash: Mapped[str] = mapped_column(String(255))

    # 用户余额，下单时扣减，取消订单时退回。
    # 阶段 4 那条"副作用回滚"测试断言的就是这个字段有没有正确恢复。
    balance: Mapped[float] = mapped_column(Float, default=0.0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    # relationship：声明一个"虚拟字段"，不对应真实的数据库列，
    # 而是让你能写 user.orders 直接拿到这个用户的所有订单（ORM 自动帮你
    # 拼接对应的 SQL 查询）。back_populates 表示这是双向关联的一端，
    # 另一端是 Order.user。
    orders: Mapped[list["Order"]] = relationship(back_populates="user")


class Product(Base):
    """商品表。对应 tests/test_products.py 里所有商品接口测试的操作对象。"""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # index=True：商品列表接口支持按 keyword 搜索名字，加索引让这类查询更快
    name: Mapped[str] = mapped_column(String(100), index=True)

    price: Mapped[float] = mapped_column(Float)

    # 库存。下单会扣减这个值（见 orders.py 的 create_order），
    # 取消订单会加回来。stock=0 时商品理论上不该再被买到。
    stock: Mapped[int] = mapped_column(Integer, default=0)

    # on_sale（在售）/ off_sale（已下架）。
    # DELETE /api/products/{id} 实际是软删除，只把这个字段改成 off_sale，
    # 不会真的删记录——这样历史订单里的商品引用不会失效。
    status: Mapped[str] = mapped_column(String(20), default="on_sale")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Order(Base):
    """订单表。对应 tests/test_order.py 的核心操作对象，也是全项目
    业务逻辑最厚的一块——涉及库存扣减、余额扣减、状态机流转。
    """

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # ForeignKey("users.id")：外键，声明这一列的值必须是 users 表里
    # 某一行的 id——数据库层面保证"订单必须属于一个真实存在的用户"，
    # 不会出现指向不存在用户的野订单。
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))

    quantity: Mapped[int] = mapped_column(Integer)

    # 下单时的总价快照 = product.price * quantity（在 orders.py 里计算后写入）。
    # 之所以要单独存一份，而不是每次现算：即使以后商品改价了，
    # 历史订单显示的金额也应该是"当时买的价格"，不会被联动改变。
    amount: Mapped[float] = mapped_column(Float)

    # 订单状态机，只有三种取值，只能沿一个方向流转：
    #   created（已创建，默认值）
    #     ├─ pay    → paid（已支付）
    #     └─ cancel → cancelled（已取消，且会回滚库存和余额）
    # 关键约束：paid 和 cancelled 都是终态，不能再互相切换或走回 created。
    # 这就是阶段 4 要测的"状态机非法流转"场景（比如已支付的订单再 cancel → 409）。
    status: Mapped[str] = mapped_column(String(20), default="created")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    # 双向关联的另一端，对应 User.orders。
    user: Mapped["User"] = relationship(back_populates="orders")
    # 这里没写 back_populates，因为 Product 类没有反向声明
    # "一个商品对应哪些订单"这个字段——目前业务不需要从商品反查订单列表。
    product: Mapped["Product"] = relationship()
