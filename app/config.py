"""服务配置。

被测服务的可调参数集中放这里，测试侧如果要造特殊场景（比如让 token
立刻过期），可以通过环境变量启动另一个实例，而不用改代码。
"""

import os

# JWT 签名密钥。
# 注意：这是一个纯本地的演示服务，默认值仅用于开发/测试。
# 真实项目绝不能把密钥写进代码，必须从环境变量或密钥管理服务读取。
SECRET_KEY = os.getenv("APP_SECRET_KEY", "dev-only-secret-do-not-use-in-prod")

ALGORITHM = "HS256"

# Token 有效期（分钟）。测试"过期 token"场景时可以设成 0 再启一个实例。
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("APP_TOKEN_EXPIRE_MINUTES", "60"))

# 数据库地址。默认落地成项目根目录下的 sqlite 文件。
DATABASE_URL = os.getenv("APP_DATABASE_URL", "sqlite:///./shop.db")

# 新注册用户的初始余额，方便测试下单扣款。
DEFAULT_USER_BALANCE = float(os.getenv("APP_DEFAULT_BALANCE", "1000"))
