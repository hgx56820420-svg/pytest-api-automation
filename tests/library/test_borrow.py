"""图书借阅：借书 / 归还的副作用和失败不变量，图书管理权限。"""

import pytest


def _snapshot(db, user, book_id):
    return {
        "deposit": db.row("users", user.id)["deposit"],
        "available": db.row("books", book_id)["available_copies"],
        "borrows": db.count("borrows", user_id=user.id),
    }


@pytest.mark.case("TC-BORROW-001")
def test_borrow_takes_copy_and_deposit(api, db, create_book):
    reader = api.register()
    book = create_book(deposit=50, copies=2)
    before = _snapshot(db, reader, book["id"])

    response = api.post("/api/borrows", user=reader, json={"book_id": book["id"]})

    assert response.status_code == 201
    assert response.json()["status"] == "borrowed"
    after = _snapshot(db, reader, book["id"])
    assert after["available"] == before["available"] - 1
    assert after["deposit"] == before["deposit"] - 50
    assert after["borrows"] == before["borrows"] + 1


@pytest.mark.case("TC-BORROW-002")
def test_borrow_without_available_copy_changes_nothing(api, db, create_book):
    book = create_book(copies=1)
    first = api.register()
    assert api.post("/api/borrows", user=first, json={"book_id": book["id"]}).status_code == 201
    second = api.register()
    before = _snapshot(db, second, book["id"])

    response = api.post("/api/borrows", user=second, json={"book_id": book["id"]})

    assert response.status_code == 400
    assert _snapshot(db, second, book["id"]) == before


@pytest.mark.case("TC-BORROW-003")
def test_borrow_with_insufficient_deposit_changes_nothing(api, db, create_book):
    # 新用户默认押金 500
    reader = api.register()
    book = create_book(deposit=600)
    before = _snapshot(db, reader, book["id"])

    response = api.post("/api/borrows", user=reader, json={"book_id": book["id"]})

    assert response.status_code == 400
    assert _snapshot(db, reader, book["id"]) == before


@pytest.mark.case("TC-RETURN-001")
def test_return_restores_copy_and_deposit(api, db, create_book):
    reader = api.register()
    book = create_book(deposit=50, copies=2)
    deposit_before = db.row("users", reader.id)["deposit"]
    borrow = api.post("/api/borrows", user=reader, json={"book_id": book["id"]}).json()

    response = api.post(f"/api/borrows/{borrow['id']}/return", user=reader)

    assert response.status_code == 200
    assert response.json()["status"] == "returned"
    assert db.row("books", book["id"])["available_copies"] == 2
    assert db.row("users", reader.id)["deposit"] == deposit_before


@pytest.mark.case("TC-RETURN-002")
def test_return_twice_returns_409_and_refunds_once(api, db, create_book):
    reader = api.register()
    book = create_book(deposit=50)
    borrow = api.post("/api/borrows", user=reader, json={"book_id": book["id"]}).json()
    assert api.post(f"/api/borrows/{borrow['id']}/return", user=reader).status_code == 200
    deposit_after_first = db.row("users", reader.id)["deposit"]

    response = api.post(f"/api/borrows/{borrow['id']}/return", user=reader)

    assert response.status_code == 409
    assert db.row("users", reader.id)["deposit"] == deposit_after_first


@pytest.mark.case("TC-RETURN-003")
def test_return_other_users_borrow_returns_404(api, db, create_book):
    reader = api.register()
    book = create_book()
    borrow = api.post("/api/borrows", user=reader, json={"book_id": book["id"]}).json()

    response = api.post(f"/api/borrows/{borrow['id']}/return", user=api.register())

    assert response.status_code == 404
    assert db.row("borrows", borrow["id"])["status"] == "borrowed"


@pytest.mark.case("TC-LIB-PERM-001")
def test_normal_user_cannot_create_book(api, db):
    books_before = db.count("books")

    response = api.post(
        "/api/books", user=api.register(), json={"title": "x", "author": "a", "deposit": 1, "total_copies": 1}
    )

    assert response.status_code == 403
    assert db.count("books") == books_before
