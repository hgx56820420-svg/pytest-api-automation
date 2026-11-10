"""Meeting 服务配置。环境变量前缀 MEETING_*，与其他被测服务隔离。"""

import os

from examples.common_auth import load_secret

MEETING_DATABASE_URL = os.getenv("MEETING_DATABASE_URL", "sqlite:///./meeting.db")

# JWT 签名密钥，和其他服务互不相同；没配置时每次启动随机生成。
SECRET_KEY = load_secret("MEETING_SECRET_KEY")

# 新注册用户的预存余额，用于预约扣费。
DEFAULT_USER_BALANCE = float(os.getenv("MEETING_DEFAULT_BALANCE", "1000"))
