"""REST client for TrainTicket's ts-gateway-service.

Endpoint paths and auth header format confirmed by reading the Spring
controllers directly (see docs/superpowers/specs/2026-09-02-workflow-trace-harness-design.md).
"""
import json
import urllib.request
import urllib.error


def request_json(method: str, url: str, *, headers: dict | None = None,
                  json_body: dict | None = None, timeout: float = 15.0) -> dict:
    data = json.dumps(json_body).encode() if json_body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


class GatewayClient:
    def __init__(self, base_url: str = "http://localhost:18888", timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _auth(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    def login(self, username: str, password: str) -> dict:
        return request_json(
            "POST", f"{self.base_url}/api/v1/users/login",
            json_body={"username": username, "password": password, "verificationCode": ""},
            timeout=self.timeout,
        )

    def list_routes(self) -> list[dict]:
        resp = request_json("GET", f"{self.base_url}/api/v1/routeservice/routes", timeout=self.timeout)
        return resp.get("data") or []

    def search_left(self, start_place: str, end_place: str, date: str, token: str, service: str) -> list[dict]:
        path = "travelservice" if service == "travel" else "travel2service"
        resp = request_json(
            "POST", f"{self.base_url}/api/v1/{path}/trips/left",
            headers=self._auth(token),
            json_body={"startPlace": start_place, "endPlace": end_place, "departureTime": date},
            timeout=self.timeout,
        )
        return resp.get("data") or []

    def list_contacts(self, account_id: str, token: str) -> list[dict]:
        resp = request_json(
            "GET", f"{self.base_url}/api/v1/contactservice/contacts/account/{account_id}",
            headers=self._auth(token), timeout=self.timeout,
        )
        return resp.get("data") or []

    def create_contact(self, account_id: str, name: str, document_type: int,
                        document_number: str, phone_number: str, token: str) -> dict:
        return request_json(
            "POST", f"{self.base_url}/api/v1/contactservice/contacts",
            headers=self._auth(token),
            json_body={
                "accountId": account_id, "name": name, "documentType": document_type,
                "documentNumber": document_number, "phoneNumber": phone_number,
            },
            timeout=self.timeout,
        )

    def preserve(self, order: dict, token: str) -> dict:
        return request_json(
            "POST", f"{self.base_url}/api/v1/preserveservice/preserve",
            headers=self._auth(token), json_body=order, timeout=self.timeout,
        )

    def preserve_other(self, order: dict, token: str) -> dict:
        return request_json(
            "POST", f"{self.base_url}/api/v1/preserveotherservice/preserveOther",
            headers=self._auth(token), json_body=order, timeout=self.timeout,
        )

    def _find_notpaid_order(self, path: str, account_id: str, trip_id: str,
                             travel_date: str, token: str) -> dict | None:
        # The server-side state filter (enableStateQuery=True) NPEs
        # deterministically on this cluster -- reproduced even with a single
        # freshly created order on a brand-new account, on both
        # ts-order-service and ts-order-other-service. Fetch unfiltered
        # (enableStateQuery=False, which returns the same rows incl. a
        # `status` field) and filter for NOTPAID (status 0) here.
        resp = request_json(
            "POST", f"{self.base_url}/api/v1/{path}",
            headers=self._auth(token),
            json_body={
                "loginId": account_id, "enableStateQuery": False, "state": 0,
                "enableBoughtDateQuery": False, "enableTravelDateQuery": False,
            },
            timeout=self.timeout,
        )
        orders = resp.get("data") or []
        matches = [
            o for o in orders
            if o.get("status") == 0 and o.get("trainNumber") == trip_id and o.get("travelDate") == travel_date
        ]
        return matches[-1] if matches else None

    def find_notpaid_order(self, account_id: str, trip_id: str, travel_date: str, token: str) -> dict | None:
        """Orders booked via preserve() (travelservice/G,D trips)."""
        return self._find_notpaid_order("orderservice/order/query", account_id, trip_id, travel_date, token)

    def find_notpaid_order_other(self, account_id: str, trip_id: str, travel_date: str, token: str) -> dict | None:
        """Orders booked via preserve_other() (travel2service/Z,T,K trips) --
        these live in a separate table, queried through a separate service."""
        return self._find_notpaid_order("orderOtherService/orderOther/query", account_id, trip_id, travel_date, token)

    def pay(self, order_id: str, trip_id: str, user_id: str, price: str, token: str) -> dict:
        return request_json(
            "POST", f"{self.base_url}/api/v1/inside_pay_service/inside_payment",
            headers=self._auth(token),
            json_body={"orderId": order_id, "tripId": trip_id, "userId": user_id, "price": price},
            timeout=self.timeout,
        )
