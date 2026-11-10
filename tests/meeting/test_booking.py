"""会议室预约：预约扣费、时间冲突、取消退款，会议室管理权限。"""

import pytest

SLOT = {"start_time": "2031-03-01T09:00:00Z", "end_time": "2031-03-01T11:00:00Z"}
OVERLAP = {"start_time": "2031-03-01T10:00:00Z", "end_time": "2031-03-01T12:00:00Z"}


@pytest.mark.case("TC-BOOK-001")
def test_booking_charges_price_times_hours(api, db, create_room):
    user = api.register()
    room = create_room(hourly_price=100)
    balance_before = db.row("users", user.id)["balance"]

    response = api.post("/api/bookings", user=user, json={"room_id": room["id"], **SLOT})

    assert response.status_code == 201
    assert response.json()["amount"] == 200.0
    assert db.row("users", user.id)["balance"] == balance_before - 200.0


@pytest.mark.case("TC-BOOK-002")
def test_overlapping_booking_returns_409_and_changes_nothing(api, db, create_room):
    room = create_room()
    assert api.post("/api/bookings", user=api.register(), json={"room_id": room["id"], **SLOT}).status_code == 201
    user = api.register()
    balance_before = db.row("users", user.id)["balance"]

    response = api.post("/api/bookings", user=user, json={"room_id": room["id"], **OVERLAP})

    assert response.status_code == 409
    assert db.row("users", user.id)["balance"] == balance_before
    assert db.count("bookings", user_id=user.id) == 0


@pytest.mark.case("TC-BOOK-003")
def test_cancel_booking_refunds_amount(api, db, create_room):
    user = api.register()
    room = create_room(hourly_price=100)
    balance_before = db.row("users", user.id)["balance"]
    booking = api.post("/api/bookings", user=user, json={"room_id": room["id"], **SLOT}).json()

    response = api.post(f"/api/bookings/{booking['id']}/cancel", user=user)

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert db.row("users", user.id)["balance"] == balance_before


@pytest.mark.case("TC-BOOK-004")
def test_cancel_twice_returns_409_and_refunds_once(api, db, create_room):
    user = api.register()
    room = create_room()
    booking = api.post("/api/bookings", user=user, json={"room_id": room["id"], **SLOT}).json()
    assert api.post(f"/api/bookings/{booking['id']}/cancel", user=user).status_code == 200
    balance_after_first = db.row("users", user.id)["balance"]

    response = api.post(f"/api/bookings/{booking['id']}/cancel", user=user)

    assert response.status_code == 409
    assert db.row("users", user.id)["balance"] == balance_after_first


@pytest.mark.case("TC-BOOK-005")
def test_overlap_check_compares_instants_across_timezones(api, db, create_room):
    """09:00Z–11:00Z 和 17:30+08:00–18:30+08:00 是同一段时间，必须判为冲突。"""
    room = create_room()
    assert api.post("/api/bookings", user=api.register(), json={"room_id": room["id"], **SLOT}).status_code == 201
    user = api.register()
    shanghai = {"start_time": "2031-03-01T17:30:00+08:00", "end_time": "2031-03-01T18:30:00+08:00"}

    response = api.post("/api/bookings", user=user, json={"room_id": room["id"], **shanghai})

    assert response.status_code == 409
    assert db.count("bookings", user_id=user.id) == 0


@pytest.mark.case("TC-ROOM-PERM-001")
def test_normal_user_cannot_change_room_price(api, db, create_room):
    room = create_room(hourly_price=100)

    response = api.put(f"/api/rooms/{room['id']}", user=api.register(), json={"hourly_price": 0.01})

    assert response.status_code == 403
    assert db.row("rooms", room["id"])["hourly_price"] == 100
