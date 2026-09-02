from unittest.mock import patch

from tt_harness.client_gateway import GatewayClient


def _client():
    return GatewayClient(base_url="http://gw")


def test_login_posts_credentials_and_returns_response():
    captured = {}

    def fake_request_json(method, url, *, headers=None, json_body=None, timeout=15.0):
        captured.update(method=method, url=url, headers=headers, json_body=json_body)
        return {"status": 1, "msg": "login success", "data": {"userId": "u1", "username": "a", "token": "tok"}}

    with patch("tt_harness.client_gateway.request_json", side_effect=fake_request_json):
        resp = _client().login("a", "b")

    assert captured["method"] == "POST"
    assert captured["url"] == "http://gw/api/v1/users/login"
    assert captured["json_body"] == {"username": "a", "password": "b", "verificationCode": ""}
    assert resp["data"]["token"] == "tok"


def test_list_routes_requires_no_auth_header():
    captured = {}

    def fake_request_json(method, url, *, headers=None, json_body=None, timeout=15.0):
        captured.update(method=method, url=url, headers=headers)
        return {"status": 1, "msg": "ok", "data": [{"id": "r1", "startStation": "Su Zhou", "endStation": "Shang Hai"}]}

    with patch("tt_harness.client_gateway.request_json", side_effect=fake_request_json):
        routes = _client().list_routes()

    assert captured["method"] == "GET"
    assert captured["url"] == "http://gw/api/v1/routeservice/routes"
    assert captured["headers"] in (None, {})
    assert routes[0]["startStation"] == "Su Zhou"


def test_search_left_hits_travel2_path_for_travel2_service():
    captured = {}

    def fake_request_json(method, url, *, headers=None, json_body=None, timeout=15.0):
        captured.update(url=url, json_body=json_body, headers=headers)
        return {"status": 1, "msg": "ok", "data": [{"tripId": {"type": "Z", "number": "1"}}]}

    with patch("tt_harness.client_gateway.request_json", side_effect=fake_request_json):
        trips = _client().search_left("su zhou", "shang hai", "2026-09-10", "tok", service="travel2")

    assert captured["url"] == "http://gw/api/v1/travel2service/trips/left"
    assert captured["json_body"] == {"startPlace": "su zhou", "endPlace": "shang hai", "departureTime": "2026-09-10"}
    assert captured["headers"] == {"Authorization": "Bearer tok"}
    assert trips[0]["tripId"]["type"] == "Z"


def test_find_notpaid_order_returns_last_match():
    orders = [
        {"id": "o1", "trainNumber": "G1", "travelDate": "2026-09-10"},
        {"id": "o2", "trainNumber": "G1", "travelDate": "2026-09-10"},
        {"id": "o3", "trainNumber": "G2", "travelDate": "2026-09-10"},
    ]

    def fake_request_json(method, url, *, headers=None, json_body=None, timeout=15.0):
        return {"status": 1, "msg": "ok", "data": orders}

    with patch("tt_harness.client_gateway.request_json", side_effect=fake_request_json):
        order = _client().find_notpaid_order("acct", "G1", "2026-09-10", "tok")

    assert order["id"] == "o2"


def test_find_notpaid_order_returns_none_when_no_match():
    def fake_request_json(method, url, *, headers=None, json_body=None, timeout=15.0):
        return {"status": 1, "msg": "ok", "data": []}

    with patch("tt_harness.client_gateway.request_json", side_effect=fake_request_json):
        order = _client().find_notpaid_order("acct", "G1", "2026-09-10", "tok")

    assert order is None
