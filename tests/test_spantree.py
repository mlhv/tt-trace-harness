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
