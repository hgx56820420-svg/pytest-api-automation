"""Meeting 服务配置。环境变量前缀 MEETING_*，与其他被测服务隔离。"""

import os

MEETING_DATABASE_URL = os.getenv("MEETING_DATABASE_URL", "sqlite:///./meeting.db")

# 新注册用户的预存余额，用于预约扣费。
DEFAULT_USER_BALANCE = float(os.getenv("MEETING_DEFAULT_BALANCE", "1000"))
