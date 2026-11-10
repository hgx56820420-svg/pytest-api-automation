"""演示服务共用的列类型。"""

from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """统一按 UTC 存取时间。

    SQLite 不保存时区：直接用 DateTime(timezone=True) 时，写入的 +08:00 和 Z 会按墙上时间比较，
    读出来也不带时区，不符合 OpenAPI 的 date-time 格式。这里写入前统一转成 UTC，读出时补上 UTC。
    不带时区的输入按 UTC 处理。
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect):
        return None if value is None else value.replace(tzinfo=timezone.utc)
