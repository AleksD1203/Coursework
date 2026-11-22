import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import factory_engine.persistence as persistence_mod
from factory_engine import FactoryEngine, PersistenceError
from factory_engine.constants import LEFT_LANE, RIGHT_LANE, SAVE_FORMAT_VERSION


def _build_sample_factory() -> FactoryEngine:
    e = FactoryEngine(14, 10)
    e.add_resource_patch(1, 2, 2, 2, "iron-ore", 15)

    assert e.place_entity("generator", 0, 6)
    assert e.place_entity("pole", 2, 4)
    assert e.place_entity("pole", 7, 4)
    assert e.place_entity("miner", 1, 2)
    assert e.place_entity("inserter", 3, 2, "E")
    assert e.place_entity("belt", 4, 2, "E")
    assert e.place_entity("inserter", 5, 2, "E")
    assert e.place_entity("assembler", 6, 1)
    assert e.set_recipe(6, 1, "iron-plate")
    assert e.place_entity("inserter", 9, 2, "E")
    assert e.place_entity("chest", 10, 2)

    assert e.drop_item_on_belt(4, 2, "iron-ore", LEFT_LANE)
    e.tick(180)
    return e


def test_json_round_trip_and_post_load_determinism(tmp_path: Path):
    e1 = _build_sample_factory()
    save_file = tmp_path / "factory.json"
    e1.save(save_file)

    e2 = FactoryEngine.load(save_file)
    assert e2.to_json() == e1.to_json()
    e2.validate_state()

    # further simulation ticks must match byte-for-byte
    e1.tick(300)
    e2.tick(300)
    assert e2.to_json() == e1.to_json()
    e2.validate_state()


def test_corrupted_saves_raise_persistence_error():
    base = _build_sample_factory().to_dict()

    with pytest.raises(PersistenceError):
        FactoryEngine.from_json("{not valid json")

    # wrong save format version
    bad_ver = json.loads(json.dumps(base))
    bad_ver["version"] = SAVE_FORMAT_VERSION + 99
    with pytest.raises(PersistenceError):
        FactoryEngine.from_dict(bad_ver)

    # bool disguised as int
    bad_bool = json.loads(json.dumps(base))
    bad_bool["tick"] = True
    with pytest.raises(PersistenceError):
        FactoryEngine.from_dict(bad_bool)

    # duplicate item uid across belts
    dup_uid = json.loads(json.dumps(base))
    for ent in dup_uid["entities"]:
        if ent["type"] == "belt":
            ent["lanes"][RIGHT_LANE] = [
                {"uid": 1, "item_type": "iron-ore", "pos": 100},
                {"uid": 1, "item_type": "iron-ore", "pos": 200},
            ]
            break
    with pytest.raises(PersistenceError):
        FactoryEngine.from_dict(dup_uid)

    # overlapping entities in save
    overlap = json.loads(json.dumps(base))
    overlap["entities"].append({"type": "chest", "x": 1, "y": 2})
    with pytest.raises(PersistenceError):
        FactoryEngine.from_dict(overlap)


@pytest.mark.parametrize(
    "patch",
    [
        {"tick": -1},
        {"next_item_uid": 0},
        {"resources": [{"x": 99, "y": 0, "resource_type": "iron-ore", "amount": 5}]},
        {"resources": [{"x": 1, "y": 1, "resource_type": "iron-ore", "amount": 0}]},
        {
            "resources": [
                {"x": 1, "y": 1, "resource_type": "iron-ore", "amount": 5},
                {"x": 1, "y": 1, "resource_type": "iron-ore", "amount": 5},
            ]
        },
        {
            "entities": [
                {"type": "belt", "x": 0, "y": 0, "direction": "E", "lanes": [[]]}
            ]
        },
        {
            "entities": [
                {"type": "inserter", "x": 0, "y": 0, "direction": "E", "state": "BAD"}
            ]
        },
        {
            "entities": [
                {
                    "type": "inserter",
                    "x": 0,
                    "y": 0,
                    "direction": "E",
                    "held_item": None,
                    "held_uid": 7,
                }
            ]
        },
        {"entities": [{"type": "miner", "x": 0, "y": 0, "progress": -5}]},
        {
            "entities": [
                {"type": "chest", "x": 0, "y": 0, "inventory": {"iron-ore": -1}}
            ]
        },
        {
            "next_item_uid": 5,
            "entities": [
                {
                    "type": "belt",
                    "x": 0,
                    "y": 0,
                    "direction": "E",
                    "lanes": [[{"uid": 5, "item_type": "iron-ore", "pos": 100}], []],
                }
            ],
        },
    ],
)
def test_invalid_save_payloads(patch: dict):
    data = {
        "version": SAVE_FORMAT_VERSION,
        "width": 5,
        "height": 5,
        "tick": 0,
        "next_item_uid": 10,
        "resources": [],
        "entities": [],
    }
    data.update(patch)
    with pytest.raises(PersistenceError):
        FactoryEngine.from_dict(data)


def test_atomic_save_fault_injection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    save_file = tmp_path / "world.json"
    tmp_file = tmp_path / "world.json.tmp"

    e_good = _build_sample_factory()
    e_good.save(save_file)
    original_json = save_file.read_text(encoding="utf-8")

    e_new = _build_sample_factory()
    e_new.tick(50)

    def fail_mid_write(path: Path, content: str) -> None:
        path.write_text(content[:25], encoding="utf-8")
        raise OSError("simulated disk failure")

    monkeypatch.setattr(persistence_mod, "_write_text_fsync", fail_mid_write)

    with pytest.raises(PersistenceError):
        e_new.save(save_file)

    # old save remains intact and .tmp file is cleaned up
    assert not tmp_file.exists()
    assert save_file.read_text(encoding="utf-8") == original_json
    restored = FactoryEngine.load(save_file)
    assert restored.get_tick() == e_good.get_tick()


def test_load_missing_file_raises_persistence_error(tmp_path: Path):
    with pytest.raises(PersistenceError):
        FactoryEngine.load(tmp_path / "missing.json")


def test_save_load_preserves_inserter_mid_swing():
    e1 = FactoryEngine(8, 8)
    assert e1.place_entity("generator", 0, 4)
    assert e1.place_entity("pole", 2, 2)
    assert e1.place_entity("chest", 1, 1)
    assert e1.place_entity("inserter", 2, 1, "E")
    assert e1.place_entity("belt", 3, 1, "E")
    assert e1.insert_into_chest(1, 1, "iron-ore", 1)

    # stop mid-swing while holding an item
    e1.tick(4)
    ins1 = e1.get_entity_at(2, 1)
    assert ins1 is not None
    assert ins1.held_item == "iron-ore"
    assert ins1.progress > 0

    e2 = FactoryEngine.from_json(e1.to_json())
    assert e2.to_json() == e1.to_json()

    e1.tick(40)
    e2.tick(40)
    assert e2.to_json() == e1.to_json()
    assert e2.count_all_items() == {"iron-ore": 1}
    e2.validate_state()


def test_views_and_snapshot_immutability_and_rect_queries():
    e = _build_sample_factory()
    snap = e.snapshot()

    assert snap.tick == e.get_tick()
    assert len(snap.entities) == len(e.get_entities())
    assert len(snap.belt_items) == len(e.get_belt_items())

    # frozen dataclasses forbid mutation
    with pytest.raises(FrozenInstanceError):
        snap.tick = 999  # type: ignore[misc]

    first_ent = snap.entities[0]
    with pytest.raises(FrozenInstanceError):
        first_ent.x = 99  # type: ignore[misc]

    # rect filter returns only entities inside region
    sub_ents = e.get_entities(rect=(0, 5, 2, 3))
    assert {ent.entity_type for ent in sub_ents} == {"generator"}


def test_view_queries_and_belt_world_coords():
    e = FactoryEngine(10, 10)
    assert e.get_entity_at(9, 9) is None

    e.add_resource_patch(0, 0, 2, 2, "iron-ore", 10)
    e.add_resource_patch(7, 7, 2, 2, "iron-ore", 10)
    assert len(e.get_deposits(rect=(0, 0, 2, 2))) == 4

    # place belts in all 4 directions with items on both lanes
    for x, direction in ((0, "E"), (2, "W"), (4, "S"), (6, "N")):
        assert e.place_entity("belt", x, 3, direction)
        assert e.drop_item_on_belt(x, 3, "iron-ore", LEFT_LANE)
        assert e.drop_item_on_belt(x, 3, "iron-plate", RIGHT_LANE)

    all_items = e.get_belt_items()
    assert len(all_items) == 8

    # east-facing belt exact lane offsets (left=y+0.25, right=y+0.75)
    east_items = e.get_belt_items(rect=(0, 3, 1, 1))
    assert len(east_items) == 2
    by_lane = {item.lane: item for item in east_items}
    assert by_lane[LEFT_LANE].world_x == pytest.approx(0.5)
    assert by_lane[LEFT_LANE].world_y == pytest.approx(3.25)
    assert by_lane[RIGHT_LANE].world_x == pytest.approx(0.5)
    assert by_lane[RIGHT_LANE].world_y == pytest.approx(3.75)

    # every direction keeps coordinates inside its tile and separates lanes
    for x in (0, 2, 4, 6):
        pair = e.get_belt_items(rect=(x, 3, 1, 1))
        assert len(pair) == 2
        for item in pair:
            assert x < item.world_x < x + 1
            assert 3 < item.world_y < 4
        assert (pair[0].world_x, pair[0].world_y) != (pair[1].world_x, pair[1].world_y)

    assert isinstance(e.get_power_summary(), dict)