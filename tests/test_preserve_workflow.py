import random

from tt_harness.workflows.preserve import randomize_inputs, build_steps, PRESERVE_WORKFLOW


class FakeGateway:
    def __init__(self):
        self.calls = []

    def list_routes(self):
        self.calls.append(("list_routes",))
        return [{"startStation": "Su Zhou", "endStation": "Shang Hai"}]

    def login(self, username, password):
        self.calls.append(("login", username, password))
        return {"status": 1, "msg": "login success", "data": {"userId": "acct1", "username": username, "token": "tok"}}

    def search_left(self, start_place, end_place, date, token, service):
        self.calls.append(("search_left", start_place, end_place, date, token, service))
        if service == "travel":
            return [{"tripId": {"type": "G", "number": "42"}}]
        return []

    def list_contacts(self, account_id, token):
        self.calls.append(("list_contacts", account_id, token))
        return []

    def create_contact(self, account_id, name, document_type, document_number, phone_number, token):
        self.calls.append(("create_contact", account_id, token))
        return {"status": 1, "msg": "created", "data": {"id": "contact1"}}

    def preserve(self, order, token):
        self.calls.append(("preserve", order, token))
        return {"status": 1, "msg": "Success.", "data": "Success."}

    def preserve_other(self, order, token):
        self.calls.append(("preserve_other", order, token))
        return {"status": 1, "msg": "Success.", "data": "Success."}

    def find_notpaid_order(self, account_id, trip_id, travel_date, token):
        self.calls.append(("find_notpaid_order", account_id, trip_id, travel_date, token))
        return {"id": "order1", "price": "100.0"}

    def find_notpaid_order_other(self, account_id, trip_id, travel_date, token):
        self.calls.append(("find_notpaid_order_other", account_id, trip_id, travel_date, token))
        return {"id": "order1", "price": "100.0"}

    def pay(self, order_id, trip_id, user_id, price, token):
        self.calls.append(("pay", order_id, trip_id, user_id, price, token))
        return {"status": 1, "msg": "Success", "data": None}


def test_randomize_inputs_picks_route_and_valid_seat_type():
    gw = FakeGateway()
    random.seed(1)
    inputs = randomize_inputs(gw)
    assert inputs["from"] == "Su Zhou"
    assert inputs["to"] == "Shang Hai"
    assert inputs["seatType"] in (2, 3)
    assert inputs["username"] == "fdse_microservice"


def test_build_steps_has_expected_names_and_correlate_flags():
    gw = FakeGateway()
    inputs = {"from": "Su Zhou", "to": "Shang Hai", "date": "2026-09-10", "seatType": 2,
              "username": "fdse_microservice", "password": "111111"}
    steps = build_steps(gw, inputs)
    names = [s.name for s in steps]
    assert names == ["login", "search_travel", "search_travel2", "ensure_contact", "preserve", "find_order", "pay"]
    correlate_by_name = {s.name: s.correlate for s in steps}
    assert correlate_by_name["login"] is True
    assert correlate_by_name["search_travel"] is True
    assert correlate_by_name["search_travel2"] is True
    assert correlate_by_name["ensure_contact"] is False
    assert correlate_by_name["preserve"] is True
    assert correlate_by_name["find_order"] is False
    assert correlate_by_name["pay"] is True


def test_full_step_sequence_threads_context_end_to_end():
    gw = FakeGateway()
    inputs = {"from": "Su Zhou", "to": "Shang Hai", "date": "2026-09-10", "seatType": 2,
              "username": "fdse_microservice", "password": "111111"}
    steps = build_steps(gw, inputs)

    context = {}
    for step in steps:
        context.update(step.run(context))

    assert context["token"] == "tok"
    assert context["account_id"] == "acct1"
    assert context["contacts_id"] == "contact1"
    assert context["trip_id"] == "G42"
    assert context["order_id"] == "order1"
    assert context["price"] == "100.0"

    called_names = [c[0] for c in gw.calls]
    assert called_names == [
        "login", "search_left", "search_left", "list_contacts", "create_contact",
        "preserve", "find_notpaid_order", "pay",
    ]


def test_preserve_step_books_travel2_trips_through_preserve_other():
    """travelservice and travel2service trips are booked through different
    endpoints -- ts-preserve-service only recognizes travelservice trips, so a
    travel2-sourced trip must go through preserve_other, not preserve."""
    gw = FakeGateway()
    gw.search_left = lambda start, end, date, token, service: (
        [] if service == "travel" else [{"tripId": {"type": "Z", "number": "99"}}]
    )
    inputs = {"from": "Su Zhou", "to": "Shang Hai", "date": "2026-09-10", "seatType": 2,
              "username": "fdse_microservice", "password": "111111"}
    steps = build_steps(gw, inputs)

    context = {}
    for step in steps:
        context.update(step.run(context))

    assert context["trip_id"] == "Z99"
    called_names = [c[0] for c in gw.calls]
    assert "preserve_other" in called_names
    assert "preserve" not in called_names
    # travel2-sourced orders live in a separate table -- find_order must use
    # the matching lookup, not the travelservice one.
    assert "find_notpaid_order_other" in called_names
    assert "find_notpaid_order" not in called_names


def test_preserve_step_raises_when_no_trips_found():
    gw = FakeGateway()
    gw.search_left = lambda *a, **k: []
    inputs = {"from": "Su Zhou", "to": "Shang Hai", "date": "2026-09-10", "seatType": 2,
              "username": "fdse_microservice", "password": "111111"}
    steps = build_steps(gw, inputs)
    context = {}
    for step in steps:
        if step.name == "preserve":
            try:
                step.run(context)
                assert False, "expected RuntimeError"
            except RuntimeError as e:
                assert "no trips" in str(e)
            break
        context.update(step.run(context))
