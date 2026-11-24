import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tv"))
import devices

CU = "uuid:test-cu"
QN = "uuid:test-qn"


def reply(uuid: str) -> str:
    return f"HTTP/1.1 200 OK\r\nST: {devices.SSDP_ST}\r\nUSN: {uuid}::{devices.SSDP_ST}\r\n\r\n"


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setenv("CF_TV_DEVICES", str(tmp_path / "devices.toml"))
    for var in ("CF_TV_NAME", "CF_TV_IP"):
        monkeypatch.delenv(var, raising=False)
    devices.save(
        {
            "default": "cu",
            "tv": {
                "cu": {"id": CU, "address": "10.0.0.5"},
                "qn": {"id": QN, "address": "10.0.0.6"},
            },
        }
    )
    return tmp_path / "devices.toml"


@pytest.fixture
def lan(monkeypatch):
    """TVs by address; ssdp replies and REST answers come from it."""
    tvs: dict[str, str] = {}
    calls: list[str] = []
    extra: list[tuple[str, str]] = []

    def identity(address, timeout=3.0):
        calls.append(address)
        tv_id = tvs.get(address)
        return {"id": tv_id, "address": address, "model": "M", "name": ""} if tv_id else None

    monkeypatch.setattr(devices, "identity", identity)
    monkeypatch.setattr(devices, "ssdp_search", lambda wait=0: [(a, reply(i)) for a, i in tvs.items()] + extra)
    monkeypatch.setattr(devices, "_sdb_open", lambda address, timeout=0: False)
    return tvs, calls, extra


def test_selection_order(registry, lan, monkeypatch):
    tvs, _, _ = lan
    tvs.update({"10.0.0.5": CU, "10.0.0.6": QN})
    monkeypatch.setenv("CF_TV_IP", "10.9.9.9")
    assert devices.resolve() == ("cu", "10.0.0.5")
    monkeypatch.setenv("CF_TV_NAME", "qn")
    assert devices.resolve() == ("qn", "10.0.0.6")
    assert devices.resolve("cu") == ("cu", "10.0.0.5")


def test_ip_fallback_without_any_name(tmp_path, monkeypatch):
    monkeypatch.setenv("CF_TV_DEVICES", str(tmp_path / "none.toml"))
    monkeypatch.delenv("CF_TV_NAME", raising=False)
    monkeypatch.setenv("CF_TV_IP", "10.9.9.9")
    assert devices.resolve() == (None, "10.9.9.9")


def test_unknown_name_lists_known(registry, lan):
    with pytest.raises(SystemExit, match="cu, qn"):
        devices.resolve("bedroom")


def test_moved_address_is_found_and_written_back(registry, lan):
    tvs, _, _ = lan
    tvs["10.0.0.42"] = CU
    assert devices.resolve("cu") == ("cu", "10.0.0.42")
    assert devices.load()["tv"]["cu"]["address"] == "10.0.0.42"


def test_matching_address_skips_discovery(registry, lan, monkeypatch):
    tvs, _, _ = lan
    tvs["10.0.0.5"] = CU
    monkeypatch.setattr(devices, "ssdp_search", lambda wait=0: pytest.fail("searched"))
    before = registry.read_text()
    assert devices.resolve("cu") == ("cu", "10.0.0.5")
    assert registry.read_text() == before


def test_other_tv_at_stored_address_is_rejected(registry, lan, monkeypatch):
    tvs, _, _ = lan
    tvs["10.0.0.5"] = QN  # DHCP gave cu's old address to qn
    monkeypatch.setattr(devices, "_sdb_open", lambda address, timeout=0: True)
    monkeypatch.setattr(devices, "BOOT_API_WAIT", 0.0)
    with pytest.raises(SystemExit, match="not answering"):
        devices.resolve("cu")
    assert devices.load()["tv"]["cu"]["address"] == "10.0.0.5"


def test_booting_tv_waits_for_api(registry, lan, monkeypatch):
    tvs, _, _ = lan
    polls = iter([None, None, CU])
    monkeypatch.setattr(devices, "_sdb_open", lambda address, timeout=0: True)
    monkeypatch.setattr(devices.time, "sleep", lambda s: None)
    real = devices.identity

    def booting(address, timeout=3.0):
        tvs["10.0.0.5"] = next(polls)
        return real(address, timeout)

    monkeypatch.setattr(devices, "identity", booting)
    assert devices.resolve("cu") == ("cu", "10.0.0.5")


def test_save_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("CF_TV_DEVICES", str(tmp_path / "d.toml"))
    original = {
        "default": "living.room",
        "tv": {
            "living.room": {"id": CU, "address": "1.2.3.4", "note": 'say "hi" \\ bye', "mount": "wall", "inputs": 4}
        },
    }
    devices.save(original)
    with open(tmp_path / "d.toml", "rb") as handle:
        assert tomllib.load(handle) == original
    assert devices.load() == original


def test_usn_uuid():
    assert devices.usn_uuid(reply(CU)) == CU
    assert devices.usn_uuid("HTTP/1.1 200 OK\r\nusn:uuid:abc\r\n") == "uuid:abc"
    assert devices.usn_uuid("HTTP/1.1 200 OK\r\nST: x\r\n") is None


def test_scan_dedups_and_drops_non_tvs(lan):
    tvs, calls, extra = lan
    tvs["10.0.0.10"] = CU
    tvs["10.0.0.9"] = QN
    extra += [("10.0.0.10", reply(CU)), ("10.0.0.101", reply("uuid:shield"))]
    found = devices.scan()
    assert [f["address"] for f in found] == ["10.0.0.9", "10.0.0.10"]
    assert sorted(calls) == ["10.0.0.10", "10.0.0.101", "10.0.0.9"]


def test_scan_for_known_id_confirms_only_its_reply(lan):
    tvs, calls, extra = lan
    tvs["10.0.0.10"] = CU
    extra.append(("10.0.0.101", reply("uuid:shield")))
    assert devices.scan(CU) == [{"id": CU, "address": "10.0.0.10", "model": "M", "name": ""}]
    assert calls == ["10.0.0.10"]
