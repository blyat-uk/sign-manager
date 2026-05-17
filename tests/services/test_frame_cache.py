"""Tests for FrameCache — LRU with eviction and recency promotion."""

from sub_label_pos.services.frame_cache import FrameCache


def test_get_returns_none_for_missing():
    c = FrameCache(max_size=2)
    assert c.get(("a", 1.0)) is None


def test_put_then_get_returns_value():
    c = FrameCache(max_size=2)
    c.put(("a", 1.0), "img1")
    assert c.get(("a", 1.0)) == "img1"


def test_lru_evicts_oldest_when_over_capacity():
    c = FrameCache(max_size=2)
    c.put(("a", 1.0), "x")
    c.put(("a", 2.0), "y")
    c.put(("a", 3.0), "z")   # evicts ("a", 1.0)
    assert c.get(("a", 1.0)) is None
    assert c.get(("a", 2.0)) == "y"
    assert c.get(("a", 3.0)) == "z"


def test_access_promotes_recency():
    c = FrameCache(max_size=2)
    c.put(("a", 1), "x")
    c.put(("a", 2), "y")
    _ = c.get(("a", 1))           # touch (a,1) -> most recent
    c.put(("a", 3), "z")          # should evict (a,2), not (a,1)
    assert c.get(("a", 1)) == "x"
    assert c.get(("a", 2)) is None
    assert c.get(("a", 3)) == "z"


def test_put_with_existing_key_updates_value_and_promotes():
    c = FrameCache(max_size=2)
    c.put(("a", 1), "x")
    c.put(("a", 2), "y")
    c.put(("a", 1), "x2")         # update + promote
    c.put(("a", 3), "z")          # should evict (a,2), not (a,1)
    assert c.get(("a", 1)) == "x2"
    assert c.get(("a", 2)) is None


def test_clear_removes_all():
    c = FrameCache(max_size=3)
    c.put(("a", 1), "x")
    c.put(("a", 2), "y")
    c.clear()
    assert c.get(("a", 1)) is None
    assert c.get(("a", 2)) is None
