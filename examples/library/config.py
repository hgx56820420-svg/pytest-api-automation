"""Library 服务配置。环境变量与 Mini Shop（APP_*）隔离，避免互相串台。"""

import os

from examples.common_auth import load_secret

LIB_DATABASE_URL = os.getenv("LIB_DATABASE_URL", "sqlite:///./library.db")

# JWT 签名密钥，和其他服务互不相同；没配置时每次启动随机生成。
SECRET_KEY = load_secret("LIB_SECRET_KEY")

# 新注册用户的初始押金余额，方便测试借书扣押金。
DEFAULT_USER_DEPOSIT = float(os.getenv("LIB_DEFAULT_DEPOSIT", "500"))
