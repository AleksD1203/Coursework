from dataclasses import dataclass

from factory_engine.belts import BeltSystem
from factory_engine.constants import (
    LEFT_LANE,
    RIGHT_LANE,
    TILE_LEN,
    Direction,
)
from factory_engine.entities import (
    Assembler,
    Belt,
    BeltItem,
    Chest,
    Deposit,
    Entity,
    Inserter,
    Miner,
)
from factory_engine.power import PowerNetwork, PowerSystem
from factory_engine.world import World


LANE_NEAR_OFFSET = 0.25
LANE_FAR_OFFSET = 0.75


def _freeze_counts(counts: dict[str, int]) -> tuple[tuple[str, int], ...]:
    items: list[tuple[str, int]] = []
    for key in sorted(counts.keys()):
        amount = counts[key]
        if amount > 0:
            items.append((key, amount))
    return tuple(items)


def belt_item_world_coords(
    tile_x: int,
    tile_y: int,
    direction: Direction,
    lane: int,
    pos: int,
) -> tuple[float, float]:
    if lane not in (LEFT_LANE, RIGHT_LANE):
        raise ValueError(f"Invalid lane index: {lane}")

    t = pos / TILE_LEN

    if direction == Direction.E:
        wx = tile_x + t
        wy = tile_y + (LANE_NEAR_OFFSET if lane == LEFT_LANE else LANE_FAR_OFFSET)
        return (wx, wy)

    if direction == Direction.W:
        wx = tile_x + 1.0 - t
        wy = tile_y + (LANE_FAR_OFFSET if lane == LEFT_LANE else LANE_NEAR_OFFSET)
        return (wx, wy)

    if direction == Direction.S:
        wx = tile_x + (LANE_FAR_OFFSET if lane == LEFT_LANE else LANE_NEAR_OFFSET)
        wy = tile_y + t
        return (wx, wy)

    # Direction.N
    wx = tile_x + (LANE_NEAR_OFFSET if lane == LEFT_LANE else LANE_FAR_OFFSET)
    wy = tile_y + 1.0 - t
    return (wx, wy)


@dataclass(frozen=True)
class EntityView:
    entity_type: str
    x: int
    y: int
    width: int
    height: int
    direction: str | None = None
    network_id: int | None = None
    satisfaction: int = 0
    progress: int = 0
    recipe_id: str | None = None
    state: str | None = None
    held_item: str | None = None
    input_buffer: tuple[tuple[str, int], ...] = ()
    output_buffer: tuple[tuple[str, int], ...] = ()
    inventory: tuple[tuple[str, int], ...] = ()

    def input_dict(self) -> dict[str, int]:
        return dict(self.input_buffer)

    def output_dict(self) -> dict[str, int]:
        return dict(self.output_buffer)

    def inventory_dict(self) -> dict[str, int]:
        return dict(self.inventory)


@dataclass(frozen=True)
class BeltItemView:
    uid: int
    item_type: str
    tile_x: int
    tile_y: int
    lane: int
    pos: int
    world_x: float
    world_y: float


@dataclass(frozen=True)
class DepositView:
    x: int
    y: int
    resource_type: str
    amount: int


@dataclass(frozen=True)
class PowerNetworkView:
    network_id: int
    pole_coords: tuple[tuple[int, int], ...]
    generator_coords: tuple[tuple[int, int], ...]
    consumer_coords: tuple[tuple[int, int], ...]
    production_kw: int
    consumption_kw: int
    satisfaction: int


@dataclass(frozen=True)
class WorldSnapshot:
    tick: int
    width: int
    height: int
    entities: tuple[EntityView, ...]
    belt_items: tuple[BeltItemView, ...]
    deposits: tuple[DepositView, ...]
    power_networks: tuple[PowerNetworkView, ...]
    wire_edges: tuple[tuple[tuple[int, int], tuple[int, int]], ...]


def build_entity_view(
    entity: Entity,
    power_system: PowerSystem | None = None,
) -> EntityView:
    direction_str = entity.direction.value if entity.direction is not None else None
    sat = 0
    if power_system is not None:
        sat = power_system.get_satisfaction_for_entity(entity)

    progress = 0
    recipe_id: str | None = None
    state: str | None = None
    held_item: str | None = None
    input_buffer: tuple[tuple[str, int], ...] = ()
    output_buffer: tuple[tuple[str, int], ...] = ()
    inventory: tuple[tuple[str, int], ...] = ()

    if isinstance(entity, Miner):
        progress = entity.progress
        output_buffer = _freeze_counts(entity.output_buffer)
    elif isinstance(entity, Assembler):
        progress = entity.progress
        recipe_id = entity.recipe_id
        input_buffer = _freeze_counts(entity.input_buffer)
        output_buffer = _freeze_counts(entity.output_buffer)
    elif isinstance(entity, Inserter):
        progress = entity.progress
        state = entity.state.value
        held_item = entity.held_item
    elif isinstance(entity, Chest):
        inventory = _freeze_counts(entity.inventory)

    return EntityView(
        entity_type=entity.entity_type,
        x=entity.x,
        y=entity.y,
        width=entity.width,
        height=entity.height,
        direction=direction_str,
        network_id=entity.network_id,
        satisfaction=sat,
        progress=progress,
        recipe_id=recipe_id,
        state=state,
        held_item=held_item,
        input_buffer=input_buffer,
        output_buffer=output_buffer,
        inventory=inventory,
    )


def build_belt_item_view(
    belt: Belt,
    lane_index: int,
    item: BeltItem,
) -> BeltItemView:
    assert belt.direction is not None
    wx, wy = belt_item_world_coords(
        tile_x=belt.x,
        tile_y=belt.y,
        direction=belt.direction,
        lane=lane_index,
        pos=item.pos,
    )
    return BeltItemView(
        uid=item.uid,
        item_type=item.item_type,
        tile_x=belt.x,
        tile_y=belt.y,
        lane=lane_index,
        pos=item.pos,
        world_x=wx,
        world_y=wy,
    )


def build_deposit_view(deposit: Deposit) -> DepositView:
    return DepositView(
        x=deposit.x,
        y=deposit.y,
        resource_type=deposit.resource_type,
        amount=deposit.amount,
    )


def build_power_network_view(network: PowerNetwork) -> PowerNetworkView:
    poles = tuple((p.x, p.y) for p in network.poles)
    generators = tuple((g.x, g.y) for g in network.generators)
    consumers = tuple((c.x, c.y) for c in network.consumers)
    return PowerNetworkView(
        network_id=network.network_id,
        pole_coords=poles,
        generator_coords=generators,
        consumer_coords=consumers,
        production_kw=network.production_kw,
        consumption_kw=network.consumption_kw,
        satisfaction=network.satisfaction,
    )


def query_entity_views(
    world: World,
    power_system: PowerSystem | None = None,
    entity_type: str | None = None,
    rect: tuple[int, int, int, int] | None = None,
) -> tuple[EntityView, ...]:
    entities = world.query_entities(entity_type=entity_type, rect=rect)
    return tuple(build_entity_view(e, power_system) for e in entities)


def query_belt_item_views(
    world: World,
    belt_system: BeltSystem,
    rect: tuple[int, int, int, int] | None = None,
) -> tuple[BeltItemView, ...]:
    raw_items = belt_system.get_all_belt_items(world, rect=rect)
    return tuple(
        build_belt_item_view(belt, lane_idx, item)
        for belt, lane_idx, item in raw_items
    )


def query_deposit_views(
    world: World,
    rect: tuple[int, int, int, int] | None = None,
) -> tuple[DepositView, ...]:
    coords = sorted(world.resources.keys())
    views: list[DepositView] = []

    if rect is not None:
        rx, ry, rw, rh = rect
        if rw <= 0 or rh <= 0:
            return ()
        rx_end = rx + rw
        ry_end = ry + rh
    else:
        rx = ry = rx_end = ry_end = 0

    for cx, cy in coords:
        deposit = world.resources[(cx, cy)]
        if deposit.is_depleted():
            continue
        if rect is not None:
            if not (rx <= cx < rx_end and ry <= cy < ry_end):
                continue
        views.append(build_deposit_view(deposit))

    return tuple(views)


def build_world_snapshot(
    world: World,
    belt_system: BeltSystem,
    power_system: PowerSystem,
    rect: tuple[int, int, int, int] | None = None,
) -> WorldSnapshot:
    entities = query_entity_views(world, power_system=power_system, rect=rect)
    belt_items = query_belt_item_views(world, belt_system=belt_system, rect=rect)
    deposits = query_deposit_views(world, rect=rect)
    networks = tuple(
        build_power_network_view(net) for net in power_system.get_all_networks()
    )
    wire_edges = tuple(power_system.get_wire_edges())

    return WorldSnapshot(
        tick=world.tick,
        width=world.width,
        height=world.height,
        entities=entities,
        belt_items=belt_items,
        deposits=deposits,
        power_networks=networks,
        wire_edges=wire_edges,
    )