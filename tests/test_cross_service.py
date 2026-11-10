"""跨服务隔离：一个服务签发的 token 不能在另一个服务使用。"""

import pytest

from api_agent.client import ApiClient
from api_agent.evidence import Recorder


@pytest.mark.case("TC-SEC-001")
def test_mini_shop_token_is_rejected_by_library(demo_service):
    shop = ApiClient(demo_service("mini_shop"), Recorder())
    library = ApiClient(demo_service("library"), Recorder())
    try:
        shop_user = shop.register()
        # 保证 library 里存在同 id 的用户，排除"用户不存在"导致的 401
        library_user = library.register()
        for _ in range(50):
            if library_user.id >= shop_user.id:
                break
            library_user = library.register()
        assert library_user.id >= shop_user.id

        response = library.get("/api/auth/me", user=shop_user)

        assert response.status_code == 401
    finally:
        shop.close()
        library.close()
