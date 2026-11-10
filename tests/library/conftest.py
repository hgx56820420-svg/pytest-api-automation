import pytest


@pytest.fixture(scope="session")
def api_target(demo_service):
    return demo_service("library")


@pytest.fixture
def create_book(api, admin):
    def create(deposit=50.0, copies=2):
        response = api.post(
            "/api/books", user=admin, json={"title": "demo", "author": "a", "deposit": deposit, "total_copies": copies}
        )
        assert response.status_code == 201, response.text
        return response.json()

    return create
