from factory_engine import FactoryEngine
from factory_engine.constants import (
    ASSEMBLER_INPUT_LIMIT,
    ASSEMBLER_OUTPUT_LIMIT,
    INSERTER_SWING_TICKS,
    MINER_TIME_TICKS,
    RIGHT_LANE,
    SAT_ONE,
    InserterState,
)


def test_power_network_split_and_merge():
    e = FactoryEngine(20, 8)
    assert e.place_entity("generator", 0, 0)
    assert e.place_entity("pole", 2, 2)
    assert e.place_entity("miner", 3, 3)

    # second isolated island (distance 10 > 7)
    assert e.place_entity("pole", 12, 2)
    assert e.place_entity("miner", 13, 3)

    snap = e.snapshot()
    assert len(snap.power_networks) == 2
    assert len(snap.wire_edges) == 0

    m1 = e.get_entity_at(3, 3)
    m2 = e.get_entity_at(13, 3)
    assert m1 is not None and m2 is not None
    assert m1.satisfaction == SAT_ONE
    assert m2.satisfaction == 0

    # bridge pole merges both islands
    assert e.place_entity("pole", 7, 2)
    snap = e.snapshot()
    assert len(snap.power_networks) == 1
    assert len(snap.wire_edges) == 2

    m2 = e.get_entity_at(13, 3)
    assert m2 is not None and m2.satisfaction == SAT_ONE

    # removing the bridge splits them back
    assert e.remove_entity(7, 2)
    snap = e.snapshot()
    assert len(snap.power_networks) == 2

    m2 = e.get_entity_at(13, 3)
    assert m2 is not None and m2.satisfaction == 0
    e.validate_state()


def test_power_overload_halves_miner_speed():
    e = FactoryEngine(32, 10)
    assert e.place_entity("generator", 0, 0)
    assert e.place_entity("pole", 2, 2)

    # 5 poles * 4 miners = 20 miners (1800 kW) on 1 generator (900 kW) -> sat=500
    for px in (7, 12, 17, 22, 27):
        assert e.place_entity("pole", px, 2)
        assert e.place_entity("miner", px - 2, 0)
        assert e.place_entity("miner", px + 1, 0)
        assert e.place_entity("miner", px - 2, 3)
        assert e.place_entity("miner", px + 1, 3)

    e.add_resource_patch(5, 0, 2, 2, "iron-ore", 10)

    miner = e.get_entity_at(5, 0)
    assert miner is not None
    assert miner.satisfaction == SAT_ONE // 2

    # at 50% power, 1 ore takes 2 * MINER_TIME_TICKS
    e.tick(MINER_TIME_TICKS * 2 - 1)
    miner = e.get_entity_at(5, 0)
    assert miner is not None
    assert miner.output_dict() == {}

    e.tick(1)
    miner = e.get_entity_at(5, 0)
    assert miner is not None
    assert miner.output_dict() == {"iron-ore": 1}
    e.validate_state()


def test_miner_depletes_patch_and_stops():
    e = FactoryEngine(10, 10)
    e.add_resource_patch(2, 2, 2, 2, "iron-ore", 1)
    assert e.place_entity("miner", 2, 2)

    # unpowered -> no extraction
    e.tick(MINER_TIME_TICKS * 2)
    m = e.get_entity_at(2, 2)
    assert m is not None and m.output_dict() == {}

    assert e.place_entity("generator", 0, 5)
    assert e.place_entity("pole", 2, 4)

    e.tick(MINER_TIME_TICKS * 4)
    m = e.get_entity_at(2, 2)
    assert m is not None
    assert m.output_dict() == {"iron-ore": 4}
    assert e.get_deposits() == ()

    # extra ticks do not spawn phantom ore
    e.tick(MINER_TIME_TICKS * 2)
    m = e.get_entity_at(2, 2)
    assert m is not None
    assert m.output_dict() == {"iron-ore": 4}
    e.validate_state()


def test_inserter_fsm_wait_drop_and_power_loss():
    e = FactoryEngine(10, 8)
    assert e.place_entity("generator", 0, 5)
    assert e.place_entity("pole", 2, 4)
    assert e.place_entity("chest", 1, 2)
    assert e.place_entity("inserter", 2, 2, "E")
    assert e.place_entity("belt", 3, 2, "E")

    # 2 packed plates stop at 255 and 191, blocking DROP_POS=128 (191 - 128 = 63 < 64)
    for _ in range(2):
        assert e.drop_item_on_belt(3, 2, "iron-plate", RIGHT_LANE)
        e.tick(10)
    e.tick(40)

    assert e.insert_into_chest(1, 2, "iron-ore", 1)

    # tick 1: pickup -> SWING_TO_DROP
    e.tick(1)
    ins = e.get_entity_at(2, 2)
    assert ins is not None
    assert ins.state == InserterState.SWING_TO_DROP.value
    assert ins.held_item == "iron-ore"

    # advance 5 ticks mid-swing, then cut power
    e.tick(5)
    ins = e.get_entity_at(2, 2)
    assert ins is not None
    saved_progress = ins.progress
    assert saved_progress == 5 * SAT_ONE

    assert e.remove_entity(2, 4)
    e.tick(20)
    ins = e.get_entity_at(2, 2)
    assert ins is not None
    assert ins.state == InserterState.SWING_TO_DROP.value
    assert ins.progress == saved_progress
    assert ins.held_item == "iron-ore"

    # restore power -> finishes swing and blocks in WAIT_DROP
    assert e.place_entity("pole", 2, 4)
    e.tick(INSERTER_SWING_TICKS - 5)
    ins = e.get_entity_at(2, 2)
    assert ins is not None
    assert ins.state == InserterState.WAIT_DROP.value
    assert ins.held_item == "iron-ore"

    # extend belt to clear jam -> drops item and returns to IDLE
    assert e.place_entity("belt", 4, 2, "E")
    assert e.place_entity("belt", 5, 2, "E")
    e.tick(60)

    ins = e.get_entity_at(2, 2)
    assert ins is not None
    assert ins.state == InserterState.IDLE.value
    assert ins.held_item is None
    assert e.count_all_items() == {"iron-ore": 1, "iron-plate": 2}
    e.validate_state()


def test_inserter_filters_items_for_assembler():
    e = FactoryEngine(12, 8)
    assert e.place_entity("generator", 0, 5)
    assert e.place_entity("pole", 2, 4)
    assert e.place_entity("pole", 4, 5)

    assert e.place_entity("chest", 1, 2)
    assert e.place_entity("inserter", 2, 2, "E")
    assert e.place_entity("assembler", 3, 2)

    assert not e.set_recipe(1, 2, "gear")
    assert e.set_recipe(3, 2, "gear")

    # gear needs iron-plate, so inserter must ignore iron-ore
    assert e.insert_into_chest(1, 2, "iron-ore", 5)
    assert e.insert_into_chest(1, 2, "iron-plate", 2)

    e.tick(150)
    e.validate_state()

    chest = e.get_entity_at(1, 2)
    asm = e.get_entity_at(3, 2)
    assert chest is not None and asm is not None
    assert chest.inventory_dict() == {"iron-ore": 5}
    assert asm.output_dict() == {"gear": 1}


def test_full_ore_to_gear_production_chain():
    e = FactoryEngine(20, 10)
    e.add_resource_patch(1, 2, 2, 2, "iron-ore", 20)

    # power spine
    assert e.place_entity("generator", 0, 6)
    assert e.place_entity("pole", 2, 4)
    assert e.place_entity("pole", 2, 0)
    assert e.place_entity("pole", 7, 0)
    assert e.place_entity("pole", 12, 0)

    # miner -> inserter -> belt -> inserter -> plate asm -> inserter -> gear asm -> inserter -> chest
    assert e.place_entity("miner", 1, 2)
    assert e.place_entity("inserter", 3, 2, "E")
    assert e.place_entity("belt", 4, 2, "E")
    assert e.place_entity("inserter", 5, 2, "E")
    assert e.place_entity("assembler", 6, 2)
    assert e.set_recipe(6, 2, "iron-plate")
    assert e.place_entity("inserter", 9, 2, "E")
    assert e.place_entity("assembler", 10, 2)
    assert e.set_recipe(10, 2, "gear")
    assert e.place_entity("inserter", 13, 2, "E")
    assert e.place_entity("chest", 14, 2)

    e.tick(1200)
    e.validate_state()

    chest = e.get_entity_at(14, 2)
    assert chest is not None
    assert chest.inventory_dict().get("gear", 0) >= 2

    # iron mass conservation: ore=1, plate=1, gear=2
    counts = e.count_all_items()
    remaining_ore = sum(d.amount for d in e.get_deposits())
    total_mass = (
        counts.get("iron-ore", 0)
        + counts.get("iron-plate", 0)
        + counts.get("gear", 0) * 2
        + remaining_ore
    )
    assert total_mass == 80

def test_stalled_factory_unchanged_after_1000_ticks():
    e = FactoryEngine(12, 10)
    e.add_resource_patch(1, 2, 1, 1, "iron-ore", 2)

    assert e.place_entity("generator", 0, 6)
    assert e.place_entity("pole", 2, 4)
    assert e.place_entity("pole", 6, 4)
    assert e.place_entity("miner", 1, 2)
    assert e.place_entity("inserter", 3, 2, "E")
    assert e.place_entity("belt", 4, 2, "E")
    assert e.place_entity("inserter", 5, 2, "E")
    assert e.place_entity("assembler", 6, 1)
    assert e.set_recipe(6, 1, "iron-plate")

    # also keep a jammed dead-end belt in the same world
    assert e.place_entity("belt", 0, 0, "E")
    assert e.drop_item_on_belt(0, 0, "iron-plate", RIGHT_LANE)
    e.tick(10)
    assert e.drop_item_on_belt(0, 0, "iron-plate", RIGHT_LANE)

    # finish all available ore and settle into complete idle/stall
    e.tick(600)
    e.validate_state()

    asm = e.get_entity_at(6, 1)
    assert asm is not None and asm.output_dict() == {"iron-plate": 2}
    assert e.get_deposits() == ()

    before_tick = e.get_tick()
    before_state = e.to_dict()
    before_state["tick"] = 0

    e.tick(1000)
    e.validate_state()

    assert e.get_tick() == before_tick + 1000
    after_state = e.to_dict()
    after_state["tick"] = 0
    assert after_state == before_state


def test_power_consumer_prefers_powered_network_on_overlap():
    e = FactoryEngine(12, 12)

    # diagonal poles at (0, 0) and (6, 6): dist^2 = 72 > 49 (disconnected),
    # yet both cover the 3x3 assembler at (2, 2) covering x=2..4, y=2..4
    assert e.place_entity("pole", 0, 0)  # network 0 (unpowered)
    assert e.place_entity("pole", 6, 6)  # network 1 (powered)
    assert e.place_entity("generator", 7, 7)
    assert e.place_entity("assembler", 2, 2)

    asm = e.get_entity_at(2, 2)
    assert asm is not None
    assert asm.network_id == 1
    assert asm.satisfaction == SAT_ONE

    net0 = e.power_system.get_network(0)
    net1 = e.power_system.get_network(1)
    assert net0 is not None and net0.satisfaction == 0
    assert net1 is not None and net1.satisfaction == SAT_ONE
    assert e.power_system.get_total_production() == 900
    assert e.power_system.get_total_consumption() == 75

    # removing generator drops both networks to 0 -> falls back to lowest network_id
    assert e.remove_entity(7, 7)
    asm = e.get_entity_at(2, 2)
    assert asm is not None
    assert asm.network_id == 0
    assert asm.satisfaction == 0
    e.validate_state()


def test_assembler_recipe_switch_and_buffer_limits():
    e = FactoryEngine(10, 8)
    assert e.place_entity("generator", 0, 5)
    assert e.place_entity("pole", 2, 4)
    assert e.place_entity("pole", 5, 5)
    assert e.place_entity("chest", 1, 2)
    assert e.place_entity("inserter", 2, 2, "E")
    assert e.place_entity("assembler", 3, 2)

    assert e.set_recipe(3, 2, "gear")
    assert e.insert_into_chest(1, 2, "iron-plate", 35)

    # 60 ticks: 2 plates delivered, gear craft in progress
    e.tick(60)
    asm = e.get_entity_at(3, 2)
    assert asm is not None
    assert asm.progress > 0
    assert asm.output_dict() == {}

    # clearing recipe resets progress and halts crafting
    assert e.set_recipe(3, 2, None)
    e.tick(20)
    asm = e.get_entity_at(3, 2)
    assert asm is not None
    assert asm.recipe_id is None
    assert asm.progress == 0

    # re-enabling recipe fills output (10 gears = 20 plates) and input (10 plates), leaving 5 in chest
    assert e.set_recipe(3, 2, "gear")
    e.tick(1300)
    e.validate_state()

    asm = e.get_entity_at(3, 2)
    chest = e.get_entity_at(1, 2)
    assert asm is not None and chest is not None
    assert asm.output_dict() == {"gear": ASSEMBLER_OUTPUT_LIMIT}
    assert asm.input_dict() == {"iron-plate": ASSEMBLER_INPUT_LIMIT}
    assert chest.inventory_dict() == {"iron-plate": 5}