import pytest


@pytest.fixture(scope="session")
def api_target(demo_service):
    return demo_service("meeting")


@pytest.fixture
def create_room(api, admin):
    def create(hourly_price=100.0):
        response = api.post("/api/rooms", user=admin, json={"name": "demo", "capacity": 6, "hourly_price": hourly_price})
        assert response.status_code == 201, response.text
        return response.json()

    return create
