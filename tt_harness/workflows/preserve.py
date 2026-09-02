"""The login -> search -> book -> pay workflow ('preserve' flow).

Two gaps beyond what the spec's controllers list covers, both handled as
uncorrelated plumbing steps (see docs/superpowers/specs/.../design.md and
this plan's "Known gaps beyond what the spec called out" section):
  - preserve requires an existing contactsId -> `ensure_contact` step.
  - preserve's response does not carry the created orderId (it returns
    createOrder's message string as `data`, not the Order) -> `find_order`
    step looks it up via order/query filtered to NOTPAID.
"""
import random
from datetime import date, timedelta

from tt_harness.workflows.base import WorkflowDefinition, WorkflowStep

DEFAULT_USERNAME = "fdse_microservice"
DEFAULT_PASSWORD = "111111"
SEAT_TYPES = [2, 3]  # SeatClass.FIRSTCLASS, SeatClass.SECONDCLASS


def randomize_inputs(gateway) -> dict:
    # KNOWN LIMITATION (low yield): this samples uniformly over ALL routes
    # ts-route-service knows about, not over routes that actually have trips.
    # TrainTicket's seed data only provisions trips for a subset of station
    # pairs, so many route/date combinations return zero trips and do_preserve
    # raises "no trips found". That degrades safely -- the run is recorded as a
    # failure and the batch continues -- but it can waste much of a large batch.
    #
    # The legacy Selenium suite this mirrors (old-docs/ts-ui-test/.../
    # TestFlowOne.java, referenced by the implementation plan but not vendored
    # into this repo) used a FIXED route instead. No known-good route list is
    # available here to sample from. The one data point we do have is the
    # captured cluster export ~/skywalking-exports/traces_2026-08-17.jsonl,
    # where the only completed preserves resolved stations "nanjing" and
    # "shanghai" on train GaoTieOne -- but those are ts-station-service ids,
    # and list_routes() returns differently-formatted station names, so the
    # pair is not transcribed here rather than guessed at.
    #
    # Before running a large batch, measure the yield (runs_summary.csv's
    # failure_reason column) and, if it is poor, narrow this to an allowlist of
    # pairs empirically confirmed to have trips on the target cluster.
    routes = gateway.list_routes()
    route = random.choice(routes)
    departure = date.today() + timedelta(days=random.randint(5, 30))
    return {
        "from": route["startStation"],
        "to": route["endStation"],
        "date": departure.strftime("%Y-%m-%d"),
        "seatType": random.choice(SEAT_TYPES),
        "username": DEFAULT_USERNAME,
        "password": DEFAULT_PASSWORD,
    }


def build_steps(gateway, inputs: dict) -> list[WorkflowStep]:
    def do_login(ctx: dict) -> dict:
        resp = gateway.login(inputs["username"], inputs["password"])
        if resp.get("status") != 1:
            raise RuntimeError(f"login failed: {resp.get('msg')}")
        data = resp["data"]
        return {"token": data["token"], "account_id": data["userId"]}

    def do_search_travel(ctx: dict) -> dict:
        trips = gateway.search_left(inputs["from"], inputs["to"], inputs["date"], ctx["token"], service="travel")
        return {"travel_trips": trips}

    def do_search_travel2(ctx: dict) -> dict:
        trips = gateway.search_left(inputs["from"], inputs["to"], inputs["date"], ctx["token"], service="travel2")
        return {"travel2_trips": trips}

    def do_ensure_contact(ctx: dict) -> dict:
        contacts = gateway.list_contacts(ctx["account_id"], ctx["token"])
        if contacts:
            return {"contacts_id": contacts[0]["id"]}
        created = gateway.create_contact(
            account_id=ctx["account_id"],
            name="tt-harness-" + str(random.randint(1000, 9999)),
            document_type=1,
            document_number=str(random.randint(10 ** 9, 10 ** 10 - 1)),
            phone_number="1" + str(random.randint(10 ** 9, 10 ** 10 - 1)),
            token=ctx["token"],
        )
        return {"contacts_id": created["data"]["id"]}

    def do_preserve(ctx: dict) -> dict:
        # travelservice (G/D trips) and travel2service (Z/T/K trips) are booked
        # through different preserve endpoints -- ts-preserve-service only
        # recognizes travelservice trips, and ts-preserve-other-service only
        # recognizes travel2service ones. Mixing them into one pool and always
        # calling preserve() made every travel2-sourced trip fail booking with
        # "Trip not found", regardless of route/date yield.
        all_trips = (
            [(trip, gateway.preserve, gateway.find_notpaid_order, "/api/v1/preserveservice/preserve")
             for trip in (ctx.get("travel_trips") or [])]
            + [(trip, gateway.preserve_other, gateway.find_notpaid_order_other,
                "/api/v1/preserveotherservice/preserveOther")
               for trip in (ctx.get("travel2_trips") or [])]
        )
        if not all_trips:
            raise RuntimeError("no trips found for randomized route/date")
        trip, book, find_order, endpoint = random.choice(all_trips)
        trip_id = trip["tripId"]["type"] + trip["tripId"]["number"]
        order = {
            "accountId": ctx["account_id"],
            "contactsId": ctx["contacts_id"],
            "tripId": trip_id,
            "seatType": inputs["seatType"],
            "date": inputs["date"],
            "from": inputs["from"],
            "to": inputs["to"],
            "assurance": 0,
        }
        resp = book(order, ctx["token"])
        if resp.get("status") != 1:
            raise RuntimeError(f"preserve failed: {resp.get('msg')}")
        # travel2-sourced orders live in a separate table (see
        # find_notpaid_order_other's docstring) -- thread the matching lookup
        # through so do_find_order queries the right one. _endpoint (see
        # runner.py) makes trace correlation match the endpoint actually hit,
        # since preserve vs. preserveOther is only known at random.choice time.
        return {"trip_id": trip_id, "find_order": find_order, "_endpoint": endpoint}

    def do_find_order(ctx: dict) -> dict:
        order = ctx["find_order"](ctx["account_id"], ctx["trip_id"], inputs["date"], ctx["token"])
        if order is None:
            raise RuntimeError("could not find the just-created NOTPAID order")
        return {"order_id": order["id"], "price": order.get("price", "0")}

    def do_pay(ctx: dict) -> dict:
        resp = gateway.pay(
            order_id=ctx["order_id"], trip_id=ctx["trip_id"],
            user_id=ctx["account_id"], price=ctx["price"], token=ctx["token"],
        )
        if resp.get("status") != 1:
            raise RuntimeError(f"payment failed: {resp.get('msg')}")
        return {"payment_status": resp.get("msg")}

    return [
        WorkflowStep("login", "/api/v1/users/login", do_login, correlate=True),
        WorkflowStep("search_travel", "/api/v1/travelservice/trips/left", do_search_travel, correlate=True),
        WorkflowStep("search_travel2", "/api/v1/travel2service/trips/left", do_search_travel2, correlate=True),
        WorkflowStep("ensure_contact", "/api/v1/contactservice/contacts", do_ensure_contact, correlate=False),
        WorkflowStep("preserve", "/api/v1/preserveservice/preserve", do_preserve, correlate=True),
        WorkflowStep("find_order", "/api/v1/orderservice/order/query", do_find_order, correlate=False),
        WorkflowStep("pay", "/api/v1/inside_pay_service/inside_payment", do_pay, correlate=True),
    ]


PRESERVE_WORKFLOW = WorkflowDefinition(
    name="preserve",
    randomize_inputs=randomize_inputs,
    build_steps=build_steps,
)
