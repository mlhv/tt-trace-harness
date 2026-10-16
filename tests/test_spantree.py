from tt_harness.spantree import SpanTree, duration_ms, is_cross_thread, parent_key, span_key, span_ref


def _span(seg, span_id, parent_span_id, start, end, refs=None, service="svc"):
    return {
        "segmentId": seg,
        "spanId": span_id,
        "parentSpanId": parent_span_id,
        "serviceCode": service,
        "endpointName": f"{seg}/{span_id}",
        "startTime": start,
        "endTime": end,
        "refs": refs or [],
    }


def _ref(seg, span_id, kind="CROSS_PROCESS"):
    return [{"parentSegmentId": seg, "parentSpanId": span_id, "traceId": "t", "type": kind}]


A0 = _span("A", 0, -1, 0, 100)
A1 = _span("A", 1, 0, 10, 60)
B0 = _span("B", 0, -1, 12, 58, refs=_ref("A", 1))
B1 = _span("B", 1, 0, 20, 40)
ORPHAN = _span("C", 0, -1, 70, 80, refs=_ref("MISSING", 3))


def test_keys_are_segment_scoped():
    assert span_key(B0) == ("B", 0)
    assert parent_key(A1) == ("A", 0)
    assert parent_key(B0) == ("A", 1)
    assert parent_key(A0) is None
    assert span_ref(B0) == "B:0"


def test_parent_and_children_across_segments():
    tree = SpanTree([A0, A1, B0, B1])
    assert tree.parent(B0) is A1
    assert tree.children(A1) == [B0]
    assert tree.children(A0) == [A1]
    assert tree.parent(A0) is None


def test_segment_local_span_id_collisions_do_not_cross_link():
    tree = SpanTree([A0, A1, B0, B1])
    assert tree.children(B0) == [B1]
    assert B1 not in tree.children(A0)


def test_orphan_ref_is_a_root():
    tree = SpanTree([A0, A1, B0, B1, ORPHAN])
    assert tree.parent(ORPHAN) is None
    assert tree.roots() == [A0, ORPHAN]


def test_self_time_subtracts_direct_children_and_floors_at_zero():
    tree = SpanTree([A0, A1, B0, B1])
    assert duration_ms(A0) == 100.0
    assert tree.self_time_ms(A0) == 50.0
    assert tree.self_time_ms(A1) == 4.0
    assert tree.self_time_ms(B0) == 26.0
    overlapping = SpanTree([_span("X", 0, -1, 0, 10), _span("X", 1, 0, 0, 10), _span("X", 2, 0, 0, 10)])
    assert overlapping.self_time_ms(overlapping.spans[0]) == 0.0


def test_trace_root_prefers_true_root_over_orphans():
    assert SpanTree([ORPHAN, A1, B0, A0]).trace_root() is A0


def test_trace_root_falls_back_to_earliest_orphan():
    assert SpanTree([B1, B0]).trace_root() is B0
    assert SpanTree([]).trace_root() is None


def test_caller_segment_entry_climbs_cross_thread_segments():
    w0 = _span("W", 0, -1, 15, 50, refs=_ref("A", 1, kind="CROSS_THREAD"))
    w1 = _span("W", 1, 0, 20, 30)
    tree = SpanTree([A0, A1, w0, w1])
    assert is_cross_thread(w0) and not is_cross_thread(B0)
    assert tree.segment_entry(w1) is w0
    assert tree.caller_segment_entry(w1) is A0
    plain = SpanTree([A0, A1, B0, B1])
    assert plain.caller_segment_entry(B1) is B0


# A service that forwards its incoming propagation header on its own outgoing calls makes every
# downstream entry name the first exit as parent (seen in SEALAB train-ticket). The exit's peer and
# the time window identify the real caller.
def _call(seg, span_id, parent_span_id, start, end, peer, service):
    span = _span(seg, span_id, parent_span_id, start, end, service=service)
    span.update(type="Exit", peer=peer)
    return span


def _entry(seg, start, end, refs, service):
    span = _span(seg, 0, -1, start, end, refs=refs, service=service)
    span["type"] = "Entry"
    return span


TRAVEL = "ts-travel-service|tt-sealab|"
T0 = _span("T", 0, -1, 0, 100, service=TRAVEL)
T1 = _call("T", 1, 0, 10, 60, "ts-ticketinfo-service:15681", TRAVEL)
TI0 = _entry("TI", 12, 58, _ref("T", 1), "ts-ticketinfo-service|tt-sealab|")
TI1 = _call("TI", 1, 0, 14, 56, "ts-basic-service:15680", "ts-ticketinfo-service|tt-sealab|")
BA0 = _entry("BA", 16, 54, _ref("T", 1), "ts-basic-service|tt-sealab|")
BA1 = _call("BA", 1, 0, 18, 52, "ts-station-service:12345", "ts-basic-service|tt-sealab|")
ST0 = _entry("ST", 20, 50, _ref("T", 1), "ts-station-service|tt-sealab|")
FORWARDED = [T0, T1, TI0, TI1, BA0, BA1, ST0]


def test_forwarded_header_entries_are_reattached_to_the_exit_that_called_their_service():
    tree = SpanTree(FORWARDED)
    assert tree.children(T1) == [TI0]
    assert tree.parent(BA0) is TI1
    assert tree.parent(ST0) is BA1
    assert tree.self_time_ms(TI1) == 4.0  # 42 ms call, 38 ms of it in ts-basic-service


def test_an_entry_whose_ref_already_matches_the_exit_peer_is_not_moved():
    # A second exit to the same service, also enclosing the entry, must not steal it.
    other = _call("T", 2, 0, 11, 59, "ts-ticketinfo-service:15681", TRAVEL)
    tree = SpanTree([T0, T1, other, TI0])
    assert tree.parent(TI0) is T1


def test_a_mismatched_ref_without_a_matching_enclosing_exit_is_kept():
    # Peers given as addresses (no service name), or the real caller missing from the trace.
    by_address = _call("T", 1, 0, 10, 60, "10.42.0.17:8080", TRAVEL)
    entry = _entry("X", 12, 58, _ref("T", 1), "ts-seat-service")
    assert SpanTree([T0, by_address, entry]).parent(entry) is by_address
    assert SpanTree([T0, T1, ST0]).parent(ST0) is T1


def test_the_innermost_enclosing_exit_wins():
    outer = _call("TI", 1, 0, 14, 56, "ts-station-service:12345", "ts-ticketinfo-service")
    inner = _call("BA", 1, 0, 18, 52, "ts-station-service:12345", "ts-basic-service")
    tree = SpanTree([T0, T1, TI0, outer, BA0, inner, ST0])
    assert tree.parent(ST0) is inner


# The forwarded header can also name an exit whose peer IS the entry's service, but which is not its
# caller: in SEALAB `/preserve`, travel → seat → travel names preserve's exit to travel for both travel
# entries. A nested exit to the same service, running when the entry starts, is the real caller.
PRESERVE = "ts-preserve-service|tt-sealab|"
SEAT = "ts-seat-service|tt-sealab|"
P0 = _span("P", 0, -1, 0, 100, service=PRESERVE)
P1 = _call("P", 1, 0, 10, 90, "ts-travel-service:12346", PRESERVE)
TR0 = _entry("TR", 12, 88, _ref("P", 1), TRAVEL)
TR1 = _call("TR", 1, 0, 20, 60, "ts-seat-service:18898", TRAVEL)
SE0 = _entry("SE", 22, 58, _ref("P", 1), SEAT)
SE1 = _call("SE", 1, 0, 25, 35, "ts-travel-service:12346", SEAT)
TR2 = _entry("TR2", 27, 33, _ref("P", 1), TRAVEL)


def test_a_forwarded_ref_to_an_exit_to_the_same_service_yields_to_a_nested_caller():
    tree = SpanTree([P0, P1, TR0, TR1, SE0, SE1, TR2])
    assert tree.parent(TR0) is P1
    assert tree.parent(SE0) is TR1
    assert tree.parent(TR2) is SE1


# Concurrent calls from a thread pool (CUHK-SE travel service with parallel seat calls): each seat entry's
# ref names its own exit, and sibling exits to the same service from other pool-thread segments overlap
# it in time. A sibling is not a nested caller: it does not descend from the ref'd exit.
CUHK_TRAVEL = "ts-travel-service|tt-cuhk|"
CUHK_SEAT = "ts-seat-service|tt-cuhk|"
M0 = _entry("M", 0, 62, [], CUHK_TRAVEL)


def _pool_call(seg, start, end):
    local = _span(seg, 0, -1, start, end, refs=_ref("M", 0, "CROSS_THREAD"), service=CUHK_TRAVEL)
    local["type"] = "Local"
    return local, _call(seg, 1, 0, start, end, "ts-seat-service:8080", CUHK_TRAVEL)


def test_concurrent_sibling_exits_do_not_steal_each_others_entries():
    pool = [_pool_call(f"W{i}", 44, 59 + (i % 3)) for i in range(6)]
    entries = [_entry(f"S{i}", 46, 58, _ref(f"W{i}", 1), CUHK_SEAT) for i in range(6)]
    tree = SpanTree([M0, *[span for pair in pool for span in pair], *entries])
    for (_, exit_span), entry in zip(pool, entries):
        assert tree.parent(entry) is exit_span
        assert tree.children(exit_span) == [entry]
