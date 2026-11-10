"""Mini Shop 鉴权：复用 examples.common_auth，密钥和 aud 是 Mini Shop 自己的。"""

from examples.common_auth import TokenAuth, hash_password, verify_password  # noqa: F401
from examples.mini_shop.config import ACCESS_TOKEN_EXPIRE_MINUTES, SECRET_KEY
from examples.mini_shop.database import get_db
from examples.mini_shop.models import User

_auth = TokenAuth(
    secret=SECRET_KEY,
    audience="mini-shop",
    user_model=User,
    get_db=get_db,
    expire_minutes=ACCESS_TOKEN_EXPIRE_MINUTES,
)

create_access_token = _auth.create_token
get_current_user = _auth.current_user_dependency()
get_current_admin = _auth.admin_dependency(get_current_user)
