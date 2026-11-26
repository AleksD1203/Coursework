from dataclasses import dataclass

from factory_engine.belts import BeltSystem
from factory_engine.constants import (
    MAX_POS,
    TILE_LEN,
    Direction,
    EntityType,
    InserterState,
    LaneSide,
    Rect,
    parse_lane_side,
    parse_rect,
    require_strict_int,
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


LANE_OFFSET = 0.25


def belt_item_world_coords(
    tile_x: int,
    tile_y: int,
    direction: Direction,
    lane_side: LaneSide,
    pos: int,
) -> tuple[float, float]:
    """Map integer belt coordinates to continuous world (x, y) for UI rendering."""
    tx = require_strict_int(tile_x, "tile_x")
    ty = require_strict_int(tile_y, "tile_y")
    side = parse_lane_side(lane_side)
    pos_val = require_strict_int(pos, "pos")
    if pos_val < 0 or pos_val > MAX_POS:
        raise ValueError(f"Item pos out of range 0..{MAX_POS}: {pos_val}")

    dx, dy = direction.delta()
    lx, ly = direction.turn_left().delta()
    along = (pos_val / TILE_LEN) - 0.5
    perp = LANE_OFFSET if side is LaneSide.LEFT else -LANE_OFFSET

    return (
        tx + 0.5 + dx * along + lx * perp,
        ty + 0.5 + dy * along + ly * perp,
    )


@dataclass(frozen=True, slots=True)
class EntityView:
    """Read-only entity projection.

    satisfaction is in thousandths (1000 == 100% power; 0 if unpowered or non-consumer).
    progress is in thousandths of a full-power tick (miners, assemblers, inserters).
    recipe_id/input_buffer/output_buffer apply to assemblers (output_buffer also to miners);
    state/held_item apply to inserters; inventory applies to chests.
    """

    entity_type: EntityType
    x: int
    y: int
    width: int
    height: int
    tier: int = 1
    direction: Direction | None = None
    network_id: int | None = None
    satisfaction: int = 0
    progress: int = 0
    recipe_id: str | None = None
    state: InserterState | None = None
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


@dataclass(frozen=True, slots=True)
class BeltItemView:
    """Read-only snapshot of one item on a belt lane with interpolated world coordinates."""

    uid: int
    item_type: str
    tile_x: int
    tile_y: int
    lane: LaneSide
    pos: int
    world_x: float
    world_y: float


@dataclass(frozen=True, slots=True)
class DepositView:
    """Read-only view of an active single-cell ore deposit."""

    x: int
    y: int
    item_type: str
    amount: int


@dataclass(frozen=True, slots=True)
class PowerNetworkView:
    """Read-only summary of one connected power network; all coordinate tuples are sorted."""

    network_id: int
    pole_coords: tuple[tuple[int, int], ...]
    generator_coords: tuple[tuple[int, int], ...]
    consumer_coords: tuple[tuple[int, int], ...]
    production_kw: int
    consumption_kw: int
    satisfaction: int


@dataclass(frozen=True, slots=True)
class WorldSnapshot:
    """Immutable, comparable snapshot of the world state and derived topology."""

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
    power_system: PowerSystem,
) -> EntityView:
    satisfaction = power_system.get_satisfaction_for_entity(entity)

    progress = 0
    recipe_id: str | None = None
    state: InserterState | None = None
    held_item: str | None = None
    input_buffer: tuple[tuple[str, int], ...] = ()
    output_buffer: tuple[tuple[str, int], ...] = ()
    inventory: tuple[tuple[str, int], ...] = ()

    if isinstance(entity, Miner):
        progress = entity.progress
        output_buffer = entity.output_buffer.items_sorted()
    elif isinstance(entity, Assembler):
        progress = entity.progress
        recipe_id = entity.recipe_id
        input_buffer = entity.input_buffer.items_sorted()
        output_buffer = entity.output_buffer.items_sorted()
    elif isinstance(entity, Inserter):
        progress = entity.progress
        state = entity.state
        held_item = entity.held.item_type if entity.held is not None else None
    elif isinstance(entity, Chest):
        inventory = entity.inventory.items_sorted()

    return EntityView(
        entity_type=entity.entity_type,
        x=entity.x,
        y=entity.y,
        width=entity.width,
        height=entity.height,
        tier=entity.tier,
        direction=entity.direction,
        network_id=entity.network_id,
        satisfaction=satisfaction,
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
    lane_side: LaneSide,
    item: BeltItem,
) -> BeltItemView:
    wx, wy = belt_item_world_coords(
        tile_x=belt.x,
        tile_y=belt.y,
        direction=belt.direction,
        lane_side=lane_side,
        pos=item.pos,
    )
    return BeltItemView(
        uid=item.uid,
        item_type=item.item_type,
        tile_x=belt.x,
        tile_y=belt.y,
        lane=lane_side,
        pos=item.pos,
        world_x=wx,
        world_y=wy,
    )


def build_deposit_view(deposit: Deposit) -> DepositView:
    return DepositView(
        x=deposit.x,
        y=deposit.y,
        item_type=deposit.item_type,
        amount=deposit.amount,
    )


def build_power_network_view(network: PowerNetwork) -> PowerNetworkView:
    return PowerNetworkView(
        network_id=network.network_id,
        pole_coords=tuple(sorted((p.x, p.y) for p in network.poles)),
        generator_coords=tuple(sorted((g.x, g.y) for g in network.generators)),
        consumer_coords=tuple(sorted((c.x, c.y) for c in network.consumers)),
        production_kw=network.production_kw,
        consumption_kw=network.consumption_kw,
        satisfaction=network.satisfaction,
    )


def query_entity_views(
    world: World,
    power_system: PowerSystem,
    entity_type: str | EntityType | None = None,
    rect: Rect | tuple[int, int, int, int] | None = None,
) -> tuple[EntityView, ...]:
    entities = world.query_entities(entity_type=entity_type, rect=rect)
    return tuple(build_entity_view(entity, power_system) for entity in entities)


def query_belt_item_views(
    world: World,
    belt_system: BeltSystem,
    rect: Rect | tuple[int, int, int, int] | None = None,
) -> tuple[BeltItemView, ...]:
    raw_items = belt_system.get_all_belt_items(world, rect=rect)
    return tuple(
        build_belt_item_view(belt, lane_side, item)
        for belt, lane_side, item in raw_items
    )


def query_deposit_views(
    world: World,
    rect: Rect | tuple[int, int, int, int] | None = None,
) -> tuple[DepositView, ...]:
    return tuple(build_deposit_view(d) for d in world.query_deposits(rect=rect))


def build_world_snapshot(
    world: World,
    belt_system: BeltSystem,
    power_system: PowerSystem,
    rect: Rect | tuple[int, int, int, int] | None = None,
) -> WorldSnapshot:
    parsed_rect = parse_rect(rect) if rect is not None else None
    entities = query_entity_views(world, power_system, rect=parsed_rect)
    belt_items = query_belt_item_views(world, belt_system, rect=parsed_rect)
    deposits = query_deposit_views(world, rect=parsed_rect)

    all_networks = power_system.get_all_networks()
    all_edges = power_system.get_wire_edges()

    if parsed_rect is not None:
        visible_net_ids = {
            entity.network_id for entity in entities if entity.network_id is not None
        }
        networks = tuple(
            build_power_network_view(net)
            for net in all_networks
            if net.network_id in visible_net_ids
        )
        wire_edges = tuple(
            edge
            for edge in all_edges
            if parsed_rect.overlaps_box(edge[0][0], edge[0][1], 1, 1)
            or parsed_rect.overlaps_box(edge[1][0], edge[1][1], 1, 1)
        )
    else:
        networks = tuple(build_power_network_view(net) for net in all_networks)
        wire_edges = tuple(all_edges)

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