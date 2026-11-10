"""服务配置。

被测服务的可调参数集中放这里，测试侧如果要造特殊场景（比如让 token
立刻过期），可以通过环境变量启动另一个实例，而不用改代码。
"""

import os

from examples.common_auth import load_secret

# JWT 签名密钥。没配置 APP_SECRET_KEY 时每次启动随机生成，代码里不放默认密钥。
SECRET_KEY = load_secret("APP_SECRET_KEY")


# Token 有效期（分钟）。测试"过期 token"场景时可以设成 0 再启一个实例。
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("APP_TOKEN_EXPIRE_MINUTES", "60"))

# 数据库地址。默认落地成项目根目录下的 sqlite 文件。
DATABASE_URL = os.getenv("APP_DATABASE_URL", "sqlite:///./shop.db")

# 新注册用户的初始余额，方便测试下单扣款。
DEFAULT_USER_BALANCE = float(os.getenv("APP_DEFAULT_BALANCE", "1000"))
