import pytest
from web_research_sim.core import Store, SimError


def test_follow_is_bound_to_source_links_and_frozen_versions(tmp_path):
    time = ["2026-01-01T00:00:00+00:00"]
    store = Store(tmp_path, clock=lambda: time[0])
    world = store.create_world("Fixture links")
    target = "https://example.org/target"
    missing = "https://example.org/missing"
    def add(url, text, links=()):
        return store.add_capture(world["id"], url=url, body=text.encode(), text=text,
                                 captured_at=time[0], links=links)
    source = add("https://example.org/source", "Read the target", [target, missing])
    first = add(target, "Original evidence")
    snap = store.freeze(world["id"])
    time[0] = "2026-01-02T00:00:00+00:00"
    add(target, "Later evidence")
    replay = store.replay(snap["id"])
    assert replay.follow(source["id"], target)["id"] == first["id"]
    with pytest.raises(SimError) as err:
        replay.follow(source["id"], missing)
    assert err.value.code == "archive_miss"
    with pytest.raises(SimError) as err:
        replay.follow(source["id"], "https://example.org/not-linked")
    assert err.value.code == "link_not_in_source"
