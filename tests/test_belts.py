from factory_engine import FactoryEngine
from factory_engine.constants import (
    BELT_SPEED,
    DROP_POS,
    ITEM_SPACING,
    LEFT_LANE,
    MAX_POS,
    RIGHT_LANE,
    TILE_LEN,
)


def test_straight_belt_and_transition():
    e = FactoryEngine(5, 5)
    assert e.place_entity("belt", 0, 0, "E")
    assert e.place_entity("belt", 1, 0, "E")
    assert e.drop_item_on_belt(0, 0, "iron-ore", LEFT_LANE)

    e.tick(1)
    items = e.get_belt_items()
    assert len(items) == 1
    assert (items[0].tile_x, items[0].tile_y) == (0, 0)
    assert items[0].pos == DROP_POS + BELT_SPEED

    # 128 + 16 * 8 = 256 -> crosses onto (1, 0) at pos=0
    e.tick(15)
    items = e.get_belt_items()
    assert (items[0].tile_x, items[0].tile_y) == (1, 0)
    assert items[0].lane == LEFT_LANE
    assert items[0].pos == 0

    e.tick(1)
    assert e.get_belt_items()[0].pos == BELT_SPEED
    e.validate_state()


def test_backpressure_dead_end_and_boundary():
    e = FactoryEngine(6, 3)
    for x in range(3):
        assert e.place_entity("belt", x, 0, "E")

    dropped = 0
    for _ in range(100):
        if dropped < 5 and e.drop_item_on_belt(0, 0, "iron-ore", LEFT_LANE):
            dropped += 1
        e.tick(1)
        e.validate_state()

    assert dropped == 5
    e.tick(150)
    e.validate_state()

    b1 = e.world.get_belt_at(1, 0)
    b2 = e.world.get_belt_at(2, 0)
    assert b1 is not None and b2 is not None

    assert [i.pos for i in b2.lanes[LEFT_LANE].items] == [255, 191, 127, 63]
    assert [i.pos for i in b1.lanes[LEFT_LANE].items] == [255]
    assert e.count_all_items() == {"iron-ore": 5}


def test_curve_preserves_lanes():
    e = FactoryEngine(5, 5)
    assert e.place_entity("belt", 0, 0, "E")
    assert e.place_entity("belt", 1, 0, "S")

    assert e.drop_item_on_belt(0, 0, "iron-ore", LEFT_LANE)
    assert e.drop_item_on_belt(0, 0, "iron-plate", RIGHT_LANE)

    e.tick(20)
    e.validate_state()

    b0 = e.world.get_belt_at(0, 0)
    assert b0 is not None
    assert b0.side_load_target_lane is None

    by_type = {i.item_type: i for i in e.get_belt_items()}
    assert (by_type["iron-ore"].tile_x, by_type["iron-ore"].tile_y) == (1, 0)
    assert by_type["iron-ore"].lane == LEFT_LANE
    assert (by_type["iron-plate"].tile_x, by_type["iron-plate"].tile_y) == (1, 0)
    assert by_type["iron-plate"].lane == RIGHT_LANE


def test_head_on_belts_do_not_connect():
    e = FactoryEngine(5, 3)
    assert e.place_entity("belt", 0, 0, "E")
    assert e.place_entity("belt", 1, 0, "W")

    assert e.drop_item_on_belt(0, 0, "iron-ore", LEFT_LANE)
    assert e.drop_item_on_belt(1, 0, "iron-plate", LEFT_LANE)

    e.tick(40)
    e.validate_state()

    b0 = e.world.get_belt_at(0, 0)
    b1 = e.world.get_belt_at(1, 0)
    assert b0 is not None and b1 is not None
    assert b0.next_belt is None
    assert b1.next_belt is None
    assert b0.lanes[LEFT_LANE].items[0].pos == MAX_POS
    assert b1.lanes[LEFT_LANE].items[0].pos == MAX_POS


def test_side_loading_drop_pos_and_stall():
    e = FactoryEngine(6, 4)
    assert e.place_entity("belt", 0, 1, "E")
    assert e.place_entity("belt", 1, 1, "E")
    assert e.place_entity("belt", 2, 1, "E")
    assert e.place_entity("belt", 1, 2, "N")

    assert e.drop_item_on_belt(0, 1, "iron-ore", RIGHT_LANE)
    assert e.drop_item_on_belt(1, 2, "iron-plate", LEFT_LANE)

    e.tick(16)
    e.validate_state()

    side = e.world.get_belt_at(1, 2)
    target = e.world.get_belt_at(1, 1)
    assert side is not None and target is not None
    assert side.side_load_target_lane == RIGHT_LANE

    by_type = {i.item_type: i for i in e.get_belt_items()}
    assert (by_type["iron-plate"].tile_x, by_type["iron-plate"].tile_y) == (1, 1)
    assert by_type["iron-plate"].lane == RIGHT_LANE
    assert by_type["iron-plate"].pos == DROP_POS
    assert abs(by_type["iron-plate"].pos - by_type["iron-ore"].pos) >= ITEM_SPACING

    # jam (1, 1) and verify side-loaded item stalls at pos=255
    assert e.remove_entity(2, 1)
    e.tick(80)
    for _ in range(10):
        e.drop_item_on_belt(0, 1, "iron-ore", RIGHT_LANE)
        e.drop_item_on_belt(1, 2, "iron-plate", LEFT_LANE)
        e.tick(10)
        e.validate_state()
    e.tick(40)
    e.validate_state()

    assert target.lanes[RIGHT_LANE].count() == 4
    side_front = side.lanes[LEFT_LANE].front_item()
    assert side_front is not None
    assert side_front.pos == MAX_POS


def test_closed_loop_speed_and_circulation():
    e = FactoryEngine(4, 4)
    assert e.place_entity("belt", 0, 0, "E")
    assert e.place_entity("belt", 1, 0, "S")
    assert e.place_entity("belt", 1, 1, "W")
    assert e.place_entity("belt", 0, 1, "N")

    assert e.drop_item_on_belt(0, 0, "iron-ore", LEFT_LANE)
    e.validate_state()

    stats = e.belt_system.get_topology_stats(e.world)
    assert stats == {"belt_count": 4, "sink_count": 0, "cycle_count": 1}

    prev = e.get_belt_items()[0]
    for _ in range(200):
        e.tick(1)
        e.validate_state()
        items = e.get_belt_items()
        assert len(items) == 1
        cur = items[0]
        if (cur.tile_x, cur.tile_y) == (prev.tile_x, prev.tile_y):
            step = cur.pos - prev.pos
        else:
            step = cur.pos + TILE_LEN - prev.pos
        assert step == BELT_SPEED
        prev = cur

    added = 1
    for _ in range(200):
        if added < 8 and e.drop_item_on_belt(0, 0, "iron-ore", LEFT_LANE):
            added += 1
        e.tick(1)
        e.validate_state()
        assert len(e.get_belt_items()) == added

    assert added == 8


def test_deferred_side_load_in_loop():
    e = FactoryEngine(5, 5)
    # straight feeder (0, 1) -> (1, 1) makes (1, 0) -> (1, 1) a side-loader
    assert e.place_entity("belt", 0, 1, "E")
    assert e.place_entity("belt", 1, 0, "S")
    assert e.place_entity("belt", 1, 1, "E")
    assert e.place_entity("belt", 2, 1, "N")
    assert e.place_entity("belt", 2, 0, "W")

    e._sync_systems()
    break_belt = e.world.get_belt_at(1, 0)
    assert break_belt is not None
    assert break_belt.side_load_target_lane == LEFT_LANE
    assert e.belt_system.update_order[0] is break_belt

    injected = 0
    for t in range(250):
        if t % 12 == 0 and injected < 14:
            if e.drop_item_on_belt(0, 1, "iron-ore", LEFT_LANE):
                injected += 1
        if t % 16 == 0 and injected < 14:
            if e.drop_item_on_belt(2, 0, "iron-plate", RIGHT_LANE):
                injected += 1
        e.tick(1)
        e.validate_state()
        assert sum(e.count_all_items().values()) == injected

    assert injected >= 10