import pytest

from factory_engine import FactoryEngine
from factory_engine.belts import BeltSystem
from factory_engine.constants import (
    CHEST_SLOT_LIMIT,
    ITEM_SPACING,
    LEFT_LANE,
    MAX_POS,
    MINER_BUFFER_LIMIT,
    RIGHT_LANE,
    Direction,
    get_entity_size,
    get_power_consumption,
    get_power_production,
    get_recipe,
    parse_direction,
)
from factory_engine.entities import (
    Assembler,
    Belt,
    BeltItem,
    Chest,
    Deposit,
    Inserter,
    Lane,
    Miner,
    Pole,
    create_entity,
)
from factory_engine.world import World


def test_directions():
    assert Direction.N.delta() == (0, -1)
    assert Direction.E.delta() == (1, 0)
    assert Direction.S.delta() == (0, 1)
    assert Direction.W.delta() == (-1, 0)

    assert Direction.N.opposite() == Direction.S
    assert Direction.E.opposite() == Direction.W
    assert Direction.N.turn_right() == Direction.E
    assert Direction.N.turn_left() == Direction.W

    assert parse_direction("e") == Direction.E
    assert parse_direction(Direction.S) == Direction.S
    assert parse_direction(None) is None

    with pytest.raises(ValueError):
        parse_direction("NE")


def test_constants_and_recipes():
    assert get_entity_size("belt") == (1, 1)
    assert get_entity_size("miner") == (2, 2)
    assert get_entity_size("assembler") == (3, 3)

    assert get_power_consumption("miner") == 90
    assert get_power_consumption("belt") == 0
    assert get_power_production("generator") == 900
    assert get_power_production("chest") == 0

    gear = get_recipe("gear")
    assert gear.ingredients == {"iron-plate": 2}
    assert gear.results == {"gear": 1}
    assert gear.total_progress() == 30000

    with pytest.raises(ValueError):
        get_entity_size("rocket")
    with pytest.raises(ValueError):
        get_recipe("steel")


def test_create_entity_errors():
    belt = create_entity("belt", 1, 2, "N")
    assert isinstance(belt, Belt)
    assert belt.direction == Direction.N

    with pytest.raises(ValueError):
        create_entity("belt", 1, 2)
    with pytest.raises(ValueError):
        create_entity("inserter", 1, 2, None)
    with pytest.raises(ValueError):
        create_entity("miner", 1, 2, "N")
    with pytest.raises(ValueError):
        create_entity("unknown", 0, 0)


def test_world_placement_and_removal():
    w = World(10, 10)

    # 3x3 at (2, 2) occupies x=2..4, y=2..4
    asm = w.place_entity("assembler", 2, 2)
    assert isinstance(asm, Assembler)
    assert w.entity_count() == 1

    # collisions
    assert w.place_entity("chest", 2, 2) is None
    assert w.place_entity("chest", 4, 4) is None
    assert w.place_entity("miner", 1, 1) is None

    # right next to it -> ok
    chest = w.place_entity("chest", 5, 4)
    assert isinstance(chest, Chest)
    assert w.entity_count() == 2

    # out of bounds
    assert w.place_entity("miner", 9, 9) is None
    assert w.place_entity("belt", -1, 0, "E") is None

    # remove by any occupied tile
    removed = w.remove_entity_at(4, 3)
    assert removed is asm
    assert w.get_entity_at(2, 2) is None
    assert w.remove_entity_at(2, 2) is None
    w.validate_spatial_index()


def test_resource_patch_and_depletion():
    w = World(8, 8)
    w.add_resource_patch(2, 2, 2, 2, "iron-ore", 5)

    miner = w.place_entity("miner", 2, 2)
    assert isinstance(miner, Miner)

    deps = w.get_deposits_under_entity(miner)
    assert [(d.x, d.y) for d in deps] == [(2, 2), (2, 3), (3, 2), (3, 3)]

    d0 = deps[0]
    assert d0.extract(3) == 3
    assert d0.amount == 2
    assert d0.extract(10) == 2
    assert d0.is_depleted()

    w.cleanup_depleted_resource(2, 2)
    assert w.get_resource_at(2, 2) is None


def test_lane_spacing():
    lane = Lane()
    assert lane.insert_item(BeltItem(uid=1, item_type="iron-ore", pos=200))
    assert not lane.insert_item(BeltItem(uid=2, item_type="iron-ore", pos=150))
    assert lane.insert_item(BeltItem(uid=2, item_type="iron-ore", pos=200 - ITEM_SPACING))

    assert lane.count() == 2
    front = lane.front_item()
    back = lane.back_item()
    assert front is not None and front.uid == 1
    assert back is not None and back.uid == 2

    taken = lane.take_front_item()
    assert taken is not None and taken.uid == 1
    assert lane.count() == 1
    lane.validate_invariants()

    with pytest.raises(ValueError):
        BeltItem(uid=3, item_type="iron-ore", pos=MAX_POS + 1)


def test_inserter_and_pole_reach():
    ins = Inserter(5, 5, Direction.E)
    assert ins.pickup_cell() == (4, 5)
    assert ins.drop_cell() == (6, 5)

    p1 = Pole(2, 2)
    p2 = Pole(9, 2)   # dist 7 -> ok
    p3 = Pole(10, 2)  # dist 8 -> too far
    assert p1.in_wire_reach(p2)
    assert not p1.in_wire_reach(p3)

    # 5x5 area around (2, 2) -> 0..4
    assert p1.covers_point(0, 0)
    assert p1.covers_point(4, 4)
    assert not p1.covers_point(5, 2)

def test_world_queries_neighbors_and_clear():
    with pytest.raises(ValueError):
        World(0, 5)
    with pytest.raises(ValueError):
        World(5, -1)

    w = World(10, 10)
    assert not w.can_place_entity("unknown", 0, 0)
    with pytest.raises(ValueError):
        w.get_entities_of_type("unknown")

    # resource patch accumulation, overwrite, and validation
    w.add_resource_patch(1, 1, 1, 1, "iron-ore", 5)
    w.add_resource_patch(1, 1, 1, 1, "iron-ore", 3)
    dep = w.get_resource_at(1, 1)
    assert dep is not None and dep.amount == 8

    w.add_resource_patch(1, 1, 1, 1, "copper-ore", 4)
    dep = w.get_resource_at(1, 1)
    assert dep is not None and dep.resource_type == "copper-ore" and dep.amount == 4

    with pytest.raises(ValueError):
        w.add_resource_patch(9, 9, 2, 2, "iron-ore", 5)
    with pytest.raises(ValueError):
        w.add_resource_patch(0, 0, 1, 1, "", 5)
    with pytest.raises(ValueError):
        w.add_resource_patch(0, 0, 1, 1, "iron-ore", 0)

    # neighbors and empty rect query
    north = w.place_entity("chest", 2, 1)
    east = w.place_entity("belt", 3, 2, "E")
    neighbors = w.get_neighbors(2, 2)
    assert neighbors[Direction.N] is north
    assert neighbors[Direction.E] is east
    assert neighbors[Direction.S] is None
    assert neighbors[Direction.W] is None

    assert w.query_entities(rect=(0, 0, 0, 5)) == []
    assert w.query_entities(rect=(0, 0, 5, -1)) == []

    w.tick = 42
    w.allocate_item_uid()
    w.clear()
    assert w.entity_count() == 0
    assert w.tick == 0
    assert w.next_item_uid == 1
    assert w.resources == {}
    w.validate_spatial_index()


def test_entity_buffers_limits_and_error_guards():
    with pytest.raises(ValueError):
        Deposit(0, 0, "", 5)
    with pytest.raises(ValueError):
        Deposit(0, 0, "iron-ore", -1)
    dep = Deposit(0, 0, "iron-ore", 3)
    with pytest.raises(ValueError):
        dep.extract(0)

    with pytest.raises(ValueError):
        BeltItem(uid=-1, item_type="iron-ore", pos=0)
    with pytest.raises(ValueError):
        BeltItem(uid=1, item_type="", pos=0)

    belt = Belt(0, 0, Direction.E)
    with pytest.raises(ValueError):
        belt.get_lane(2)
    assert belt.try_drop_item(1, "iron-ore", LEFT_LANE, 100)
    assert belt.try_drop_item(2, "iron-plate", RIGHT_LANE, 100)
    t1 = belt.try_take_item()
    t2 = belt.try_take_item()
    assert t1 is not None and t1.uid == 1
    assert t2 is not None and t2.uid == 2
    assert belt.try_take_item() is None

    chest = Chest(0, 0)
    assert not chest.can_add_item(0)
    with pytest.raises(ValueError):
        chest.add_item("iron-ore", 0)
    assert chest.add_item("iron-ore", CHEST_SLOT_LIMIT)
    assert not chest.add_item("iron-ore", 1)
    assert chest.take_item("copper-ore") is None
    assert chest.take_item("iron-ore") == "iron-ore"
    assert chest.take_item() == "iron-ore"
    assert chest.total_items() == CHEST_SLOT_LIMIT - 2

    miner = Miner(0, 0)
    with pytest.raises(ValueError):
        miner.add_output("iron-ore", 0)
    miner.add_output("iron-ore", MINER_BUFFER_LIMIT)
    assert not miner.can_produce()
    assert miner.has_output()
    assert miner.take_output() == "iron-ore"


def test_engine_command_error_guards():
    e = FactoryEngine(6, 6)
    with pytest.raises(ValueError):
        e.tick(-1)

    assert not e.insert_into_chest(0, 0, "iron-ore", 1)
    assert not e.drop_item_on_belt(0, 0, "iron-ore")

    assert e.place_entity("chest", 0, 0)
    assert not e.drop_item_on_belt(0, 0, "iron-ore")
    assert not e.set_recipe(0, 0, "gear")

    with pytest.raises(ValueError):
        BeltSystem(speed=0)
    with pytest.raises(ValueError):
        BeltSystem(speed=ITEM_SPACING + 1)