"""Library 服务鉴权：复用 examples.common_auth，密钥和 aud 是 Library 自己的。"""

from examples.common_auth import TokenAuth, hash_password, verify_password  # noqa: F401
from examples.library.config import SECRET_KEY
from examples.library.database import get_db
from examples.library.models import User

_auth = TokenAuth(secret=SECRET_KEY, audience="library", user_model=User, get_db=get_db)

create_access_token = _auth.create_token
get_current_user = _auth.current_user_dependency()
get_current_admin = _auth.admin_dependency(get_current_user)
