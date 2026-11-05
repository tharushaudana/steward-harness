"""Contract tests every storage adapter must pass. Reuse them for your own adapter:

    # tests/test_my_store.py
    import pytest
    from steward.testing.contracts import SessionStateContract

    class TestMySessions(SessionStateContract):
        @pytest.fixture
        def store(self):
            return MySessionStore(...)        # a fresh, empty store per test

Requires pytest and pytest-asyncio (asyncio_mode = "auto").
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from steward.errors import RevisionConflict
from steward.stores.base import LedgerEntry, NewRecord


class SessionStateContract:
    @pytest.fixture
    def store(self) -> Any:  # pragma: no cover - overridden by adapter tests
        raise NotImplementedError("override the `store` fixture")

    async def test_missing_session_is_version_zero(self, store: Any) -> None:
        assert await store.load("nope") == (None, 0)

    async def test_insert_then_update(self, store: Any) -> None:
        assert await store.compare_and_set("s1", {"slots": {"a": 1}}, 0)
        state, v = await store.load("s1")
        assert state == {"slots": {"a": 1}} and v == 1
        assert await store.compare_and_set("s1", {"slots": {"a": 2}}, 1)
        assert (await store.load("s1")) == ({"slots": {"a": 2}}, 2)

    async def test_stale_write_is_rejected_not_overwritten(self, store: Any) -> None:
        assert await store.compare_and_set("s1", {"n": 1}, 0)
        assert not await store.compare_and_set("s1", {"n": 99}, 0)  # someone else already wrote version 1
        assert (await store.load("s1")) == ({"n": 1}, 1)

    async def test_concurrent_writers_exactly_one_wins(self, store: Any) -> None:
        assert await store.compare_and_set("s1", {"n": 0}, 0)
        results = await asyncio.gather(*(store.compare_and_set("s1", {"n": i}, 1) for i in range(1, 6)))
        assert sum(results) == 1
        assert (await store.load("s1"))[1] == 2

    async def test_insert_race_exactly_one_wins(self, store: Any) -> None:
        results = await asyncio.gather(*(store.compare_and_set("new", {"n": i}, 0) for i in range(5)))
        assert sum(results) == 1

    async def test_returned_state_is_a_copy(self, store: Any) -> None:
        await store.compare_and_set("s1", {"slots": {"a": 1}}, 0)
        state, _ = await store.load("s1")
        state["slots"]["a"] = 999
        assert (await store.load("s1"))[0] == {"slots": {"a": 1}}

    async def test_delete(self, store: Any) -> None:
        await store.compare_and_set("s1", {"n": 1}, 0)
        await store.delete("s1")
        assert await store.load("s1") == (None, 0)


class RecordStoreContract:
    @pytest.fixture
    def store(self) -> Any:  # pragma: no cover
        raise NotImplementedError("override the `store` fixture")

    async def test_append_and_list(self, store: Any) -> None:
        r = await store.append(NewRecord("lead", {"name": "Ada"}, "s1", key="k1"))
        assert not r.duplicate
        rows = await store.list("lead")
        assert [x.data for x in rows] == [{"name": "Ada"}] and rows[0].id == r.id and rows[0].status == "open"

    async def test_duplicate_key_merges_non_empty_fields(self, store: Any) -> None:
        first = await store.append(NewRecord("lead", {"name": "Ada", "interest": ""}, "s1", key="k1"))
        again = await store.append(NewRecord("lead", {"name": "", "interest": "e-bike", "phone": None}, "s2", key="k1"))
        assert again.duplicate and again.enriched and again.id == first.id
        rows = await store.list("lead")
        assert len(rows) == 1 and rows[0].data == {"name": "Ada", "interest": "e-bike"}

    async def test_duplicate_without_new_data_is_not_enriched(self, store: Any) -> None:
        await store.append(NewRecord("lead", {"name": "Ada"}, "s1", key="k1"))
        again = await store.append(NewRecord("lead", {"name": "Ada"}, "s1", key="k1"))
        assert again.duplicate and not again.enriched

    async def test_concurrent_same_key_creates_one_record(self, store: Any) -> None:
        await asyncio.gather(*(store.append(NewRecord("lead", {"i": str(i)}, "s", key="same")) for i in range(5)))
        assert len(await store.list("lead")) == 1

    async def test_records_without_key_are_never_merged(self, store: Any) -> None:
        await store.append(NewRecord("note", {"a": "1"}, "s"))
        await store.append(NewRecord("note", {"a": "1"}, "s"))
        assert len(await store.list("note")) == 2

    async def test_test_records_hidden_by_default(self, store: Any) -> None:
        await store.append(NewRecord("lead", {"n": "real"}, "s1", key="a"))
        await store.append(NewRecord("lead", {"n": "test"}, "s2", key="test:a", is_test=True))
        assert [r.data["n"] for r in await store.list("lead")] == ["real"]
        assert len(await store.list("lead", include_test=True)) == 2

    async def test_filter_by_type_and_status(self, store: Any) -> None:
        a = await store.append(NewRecord("lead", {"x": "1"}, "s"))
        await store.append(NewRecord("handover", {"x": "2"}, "s"))
        assert {r.type for r in await store.list()} == {"lead", "handover"}
        await store.set_status(a.id, "done")
        assert (await store.list("lead"))[0].status == "done"


class RuntimeStoreContract:
    @pytest.fixture
    def store(self) -> Any:  # pragma: no cover
        raise NotImplementedError("override the `store` fixture")

    async def test_get_default_set_get(self, store: Any) -> None:
        assert await store.get("agents_online", False) is False
        await store.set("agents_online", True)
        assert await store.get("agents_online") is True
        await store.set("hours", {"open": "09:00"})
        assert await store.get("hours") == {"open": "09:00"}


class ConfigStoreContract:
    @pytest.fixture
    def store(self) -> Any:  # pragma: no cover
        """A store whose published config is {"name": "v1"} (version 1) and that has no draft."""
        raise NotImplementedError("override the `store` fixture")

    async def test_draft_publish_history_restore(self, store: Any) -> None:
        pub = await store.published()
        assert pub is not None and pub.config["name"] == "v1" and await store.draft() is None
        d = await store.save_draft({"name": "v2"}, pub.rev)
        assert (await store.draft()).config == {"name": "v2"}
        new = await store.publish("second")
        assert new.config["name"] == "v2" and new.version == pub.version + 1 and await store.draft() is None
        hist = await store.history()
        assert hist and hist[0].version == pub.version
        old = await store.get_version(hist[0].id)
        assert old is not None and old.config["name"] == "v1"
        assert d.rev != pub.rev

    async def test_stale_draft_save_conflicts(self, store: Any) -> None:
        pub = await store.published()
        await store.save_draft({"name": "a"}, pub.rev)
        with pytest.raises(RevisionConflict):
            await store.save_draft({"name": "b"}, pub.rev)  # based on the published rev, but a draft exists now

    async def test_discard_and_publish_without_draft(self, store: Any) -> None:
        pub = await store.published()
        await store.save_draft({"name": "x"}, pub.rev)
        await store.discard_draft()
        assert await store.draft() is None
        with pytest.raises(ValueError):
            await store.publish()


class LedgerStoreContract:
    @pytest.fixture
    def store(self) -> Any:  # pragma: no cover
        raise NotImplementedError("override the `store` fixture")

    async def test_record_total_summary(self, store: Any) -> None:
        assert await store.total() == 0
        await store.record(LedgerEntry("route", "m-a", 100, 0, 0.001))
        await store.record(LedgerEntry("answer", "m-b", 50, 20, 0.002))
        assert abs(await store.total() - 0.003) < 1e-9
        s = await store.summary()
        assert s.calls == 2 and abs(s.by_purpose["answer"] - 0.002) < 1e-9 and set(s.by_model) == {"m-a", "m-b"}


class PlanCacheContract:
    @pytest.fixture
    def store(self) -> Any:  # pragma: no cover
        raise NotImplementedError("override the `store` fixture")

    async def test_get_put_versioned_clear(self, store: Any) -> None:
        assert await store.get("q", "v1") is None
        await store.put("q", "v1", {"units": ["a"]})
        assert await store.get("q", "v1") == {"units": ["a"]}
        assert await store.get("q", "v2") is None  # another knowledge/engine version never reuses a plan
        await store.clear()
        assert await store.get("q", "v1") is None
