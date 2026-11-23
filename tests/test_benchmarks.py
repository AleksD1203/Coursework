from factory_engine import FactoryEngine
from factory_engine.constants import LEFT_LANE, RIGHT_LANE, SAT_ONE

TICK_BUDGET_SEC = 0.0166


def _build_active_factory(cols: int, rows: int) -> FactoryEngine:
    cell_w = 26
    cell_h = 6
    e = FactoryEngine(cols * cell_w, rows * cell_h)

    for row in range(rows):
        oy = row * cell_h
        for col in range(cols):
            ox = col * cell_w
            e.add_resource_patch(ox, oy + 2, 2, 2, "iron-ore", 10000)

            assert e.place_entity("generator", ox, oy)
            for px in (2, 9, 16, 23):
                assert e.place_entity("pole", ox + px, oy + 1)

            assert e.place_entity("miner", ox, oy + 2)
            assert e.place_entity("inserter", ox + 2, oy + 2, "E")
            for bx in range(3, 10):
                assert e.place_entity("belt", ox + bx, oy + 2, "E")
            assert e.place_entity("inserter", ox + 10, oy + 2, "E")

            assert e.place_entity("assembler", ox + 11, oy + 2)
            assert e.set_recipe(ox + 11, oy + 2, "iron-plate")
            assert e.place_entity("inserter", ox + 14, oy + 2, "E")
            for bx in (15, 16):
                assert e.place_entity("belt", ox + bx, oy + 2, "E")
            assert e.place_entity("inserter", ox + 17, oy + 2, "E")

            assert e.place_entity("assembler", ox + 18, oy + 2)
            assert e.set_recipe(ox + 18, oy + 2, "gear")
            assert e.place_entity("inserter", ox + 21, oy + 2, "E")
            for bx in (22, 23):
                assert e.place_entity("belt", ox + bx, oy + 2, "E")
            assert e.place_entity("inserter", ox + 24, oy + 2, "E")
            assert e.place_entity("chest", ox + 25, oy + 2)

            # seed items so all assemblers and belts are active immediately
            assert e.drop_item_on_belt(ox + 3, oy + 2, "iron-ore", LEFT_LANE)
            assert e.drop_item_on_belt(ox + 8, oy + 2, "iron-ore", RIGHT_LANE)
            assert e.drop_item_on_belt(ox + 15, oy + 2, "iron-plate", LEFT_LANE)
            assert e.drop_item_on_belt(ox + 16, oy + 2, "iron-plate", RIGHT_LANE)

    return e


def test_benchmark_active_factory_2600_entities(benchmark):
    # 10 * 10 cells * 26 entities = 2600 active entities
    e = _build_active_factory(cols=10, rows=10)
    assert e.world.entity_count() == 2600

    # warm up into steady-state production
    e.tick(250)
    e.validate_state()

    sample_asm = e.get_entity_at(11, 2)
    assert sample_asm is not None
    assert sample_asm.satisfaction == SAT_ONE

    counts = e.count_all_items()
    assert counts.get("iron-ore", 0) > 0
    assert counts.get("iron-plate", 0) > 0
    assert counts.get("gear", 0) > 0

    benchmark.pedantic(e.tick, args=(1,), rounds=30, iterations=1, warmup_rounds=5)

    if benchmark.stats is not None:
        assert benchmark.stats["mean"] < TICK_BUDGET_SEC


def test_benchmark_dense_belt_loops_2500_items(benchmark):
    width = 50
    height = 50
    e = FactoryEngine(width, height)

    # 25 closed loops of 100 belts each = 2500 belts
    for r in range(height // 2):
        y_top = r * 2
        y_bot = y_top + 1
        for x in range(width - 1):
            assert e.place_entity("belt", x, y_top, "E")
        assert e.place_entity("belt", width - 1, y_top, "S")

        for x in range(width - 1, 0, -1):
            assert e.place_entity("belt", x, y_bot, "W")
        assert e.place_entity("belt", 0, y_bot, "N")

    for y in range(height):
        for x in range(0, width, 2):
            assert e.drop_item_on_belt(x, y, "iron-ore", LEFT_LANE)
            assert e.drop_item_on_belt(x, y, "iron-plate", RIGHT_LANE)

    assert e.world.entity_count() == 2500
    assert sum(e.count_all_items().values()) == 2500

    e.tick(10)
    e.validate_state()

    benchmark.pedantic(e.tick, args=(1,), rounds=30, iterations=1, warmup_rounds=5)

    if benchmark.stats is not None:
        assert benchmark.stats["mean"] < TICK_BUDGET_SEC


def test_benchmark_json_round_trip_520_entities(benchmark):
    e = _build_active_factory(cols=5, rows=4)
    assert e.world.entity_count() == 520
    e.tick(150)

    def round_trip() -> FactoryEngine:
        return FactoryEngine.from_json(e.to_json())

    restored = benchmark.pedantic(round_trip, rounds=10, iterations=1, warmup_rounds=2)
    assert restored.to_json() == e.to_json()

def test_benchmark_snapshot_2600_entities(benchmark):
    e = _build_active_factory(cols=10, rows=10)
    e.tick(100)

    snap = benchmark.pedantic(e.snapshot, rounds=20, iterations=1, warmup_rounds=3)
    assert len(snap.entities) == 2600

    if benchmark.stats is not None:
        assert benchmark.stats["mean"] < TICK_BUDGET_SEC


def test_benchmark_topology_and_power_rebuild_2600_entities(benchmark):
    e = _build_active_factory(cols=10, rows=10)
    e.tick(10)

    def rebuild() -> None:
        e.world.belts_dirty = True
        e.world.power_dirty = True
        e._sync_systems()

    benchmark.pedantic(rebuild, rounds=20, iterations=1, warmup_rounds=3)

    if benchmark.stats is not None:
        assert benchmark.stats["mean"] < 0.05