from hypothesis import given, settings
from hypothesis import strategies as st

from factory_engine import FactoryEngine
from factory_engine.constants import (
    BELT_SPEED,
    DROP_POS,
    ITEM_SPACING,
    LEFT_LANE,
    RIGHT_LANE,
    TILE_LEN,
)

DIRS = ("N", "E", "S", "W")
ITEM_MASS = {
    "iron-ore": 1,
    "iron-plate": 1,
    "gear": 2,
}


def _total_iron_mass(e: FactoryEngine) -> int:
    counts = e.count_all_items()
    item_mass = sum(counts.get(k, 0) * m for k, m in ITEM_MASS.items())
    ore_left = sum(d.amount for d in e.get_deposits())
    return item_mass + ore_left


def _check_cross_tile_spacing(e: FactoryEngine) -> None:
    for ent in e.world.get_entities_of_type("belt"):
        belt = e.world.get_belt_at(ent.x, ent.y)
        assert belt is not None
        if belt.next_belt is None or belt.side_load_target_lane is not None:
            continue
        for lane_idx in (LEFT_LANE, RIGHT_LANE):
            front = belt.lanes[lane_idx].front_item()
            back = belt.next_belt.lanes[lane_idx].back_item()
            if front is not None and back is not None:
                gap = (back.pos + TILE_LEN) - front.pos
                assert gap >= ITEM_SPACING


@settings(max_examples=40, deadline=None)
@given(
    grid_dirs=st.lists(
        st.lists(st.one_of(st.none(), st.sampled_from(DIRS)), min_size=5, max_size=5),
        min_size=5,
        max_size=5,
    ),
    drops=st.lists(
        st.tuples(
            st.integers(0, 4),
            st.integers(0, 4),
            st.sampled_from((LEFT_LANE, RIGHT_LANE)),
            st.sampled_from(("iron-ore", "iron-plate")),
        ),
        max_size=25,
    ),
    ticks=st.integers(10, 60),
)
def test_random_belt_layouts_keep_invariants_and_items(grid_dirs, drops, ticks):
    e = FactoryEngine(5, 5)
    for y in range(5):
        for x in range(5):
            d = grid_dirs[y][x]
            if d is not None:
                assert e.place_entity("belt", x, y, d)

    dropped = 0
    for x, y, lane, item in drops:
        if e.drop_item_on_belt(x, y, item, lane):
            dropped += 1

    for _ in range(ticks):
        prev_pos = {
            item.uid: (item.tile_x, item.tile_y, item.pos)
            for item in e.get_belt_items()
        }
        e.tick(1)
        e.validate_state()
        _check_cross_tile_spacing(e)

        curr_items = e.get_belt_items()
        assert len(curr_items) == dropped

        for cur in curr_items:
            px, py, ppos = prev_pos[cur.uid]
            if (cur.tile_x, cur.tile_y) == (px, py):
                step = cur.pos - ppos
                assert 0 <= step <= BELT_SPEED
            else:
                prev_belt = e.world.get_belt_at(px, py)
                assert prev_belt is not None
                if prev_belt.side_load_target_lane is not None:
                    assert cur.pos == DROP_POS
                else:
                    step = cur.pos + TILE_LEN - ppos
                    assert 0 <= step <= BELT_SPEED


@settings(max_examples=30, deadline=None)
@given(
    specs=st.lists(
        st.tuples(
            st.sampled_from(("belt", "chest", "pole", "inserter")),
            st.integers(0, 7),
            st.integers(0, 7),
            st.sampled_from(DIRS),
        ),
        min_size=4,
        max_size=20,
        unique_by=lambda s: (s[1], s[2]),
    ),
    perm_seed=st.randoms(use_true_random=False),
    split_tick=st.integers(1, 30),
)
def test_build_order_and_midrun_save_load_determinism(specs, perm_seed, split_tick):
    shuffled = list(specs)
    perm_seed.shuffle(shuffled)

    e1 = FactoryEngine(8, 8)
    e2 = FactoryEngine(8, 8)

    for etype, x, y, d in specs:
        direction = d if etype in ("belt", "inserter") else None
        assert e1.place_entity(etype, x, y, direction)

    for etype, x, y, d in shuffled:
        direction = d if etype in ("belt", "inserter") else None
        assert e2.place_entity(etype, x, y, direction)

    # seed identical items on all placed chests and belts
    for etype, x, y, _ in sorted(specs, key=lambda s: (s[1], s[2])):
        if etype == "chest":
            e1.insert_into_chest(x, y, "iron-ore", 2)
            e2.insert_into_chest(x, y, "iron-ore", 2)
        elif etype == "belt":
            e1.drop_item_on_belt(x, y, "iron-ore", LEFT_LANE)
            e2.drop_item_on_belt(x, y, "iron-ore", LEFT_LANE)

    assert e1.to_json() == e2.to_json()

    e1.tick(split_tick)
    e2.tick(split_tick)
    assert e1.to_json() == e2.to_json()

    # round-trip via JSON mid-simulation
    e3 = FactoryEngine.from_json(e1.to_json())
    e1.tick(30)
    e2.tick(30)
    e3.tick(30)

    assert e1.to_json() == e2.to_json() == e3.to_json()
    e3.validate_state()


@settings(max_examples=25, deadline=None)
@given(
    ore_per_tile=st.integers(1, 15),
    extra_miners=st.integers(0, 6),
    second_recipe=st.sampled_from(("gear", "iron-plate", None)),
    chunks=st.lists(st.integers(1, 80), min_size=1, max_size=8),
)
def test_iron_mass_conservation_and_tick_chunking(
    ore_per_tile, extra_miners, second_recipe, chunks
):
    def build() -> FactoryEngine:
        eng = FactoryEngine(24, 12)
        eng.add_resource_patch(1, 2, 2, 2, "iron-ore", ore_per_tile)

        assert eng.place_entity("generator", 0, 7)
        assert eng.place_entity("pole", 2, 5)
        assert eng.place_entity("pole", 2, 0)
        assert eng.place_entity("pole", 7, 0)
        assert eng.place_entity("pole", 12, 0)

        assert eng.place_entity("miner", 1, 2)
        assert eng.place_entity("inserter", 3, 2, "E")
        assert eng.place_entity("belt", 4, 2, "E")
        assert eng.place_entity("inserter", 5, 2, "E")
        assert eng.place_entity("assembler", 6, 2)
        assert eng.set_recipe(6, 2, "iron-plate")
        assert eng.place_entity("inserter", 9, 2, "E")
        assert eng.place_entity("assembler", 10, 2)
        assert eng.set_recipe(10, 2, second_recipe)
        assert eng.place_entity("inserter", 13, 2, "E")
        assert eng.place_entity("chest", 14, 2)

        # optional extra load on the power grid (can trigger partial satisfaction)
        for i in range(extra_miners):
            mx = 5 + (i % 3) * 3
            my = 7 + (i // 3) * 2
            eng.place_entity("miner", mx, my)
            eng.place_entity("pole", mx, 5)

        return eng

    e_chunked = build()
    e_single = build()
    initial_mass = _total_iron_mass(e_chunked)
    assert initial_mass == ore_per_tile * 4

    for step in chunks:
        e_chunked.tick(step)
        e_chunked.validate_state()
        assert _total_iron_mass(e_chunked) == initial_mass

    e_single.tick(sum(chunks))
    assert e_chunked.to_json() == e_single.to_json()