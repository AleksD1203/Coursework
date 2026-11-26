from collections.abc import Callable
from pathlib import Path
from typing import Any

from factory_engine.belts import BeltSystem
from factory_engine.constants import (
    DROP_POS,
    MINER_TIME_TICKS,
    Direction,
    EntityType,
    LaneSide,
    Rect,
    get_recipe,
    parse_lane_side,
    require_strict_int,
    ticks_to_progress,
    validate_item_name,
)
from factory_engine.entities import (
    Assembler,
    Belt,
    Chest,
    Entity,
    HeldItem,
    IdleState,
    Inserter,
    InserterFSMState,
    Miner,
    SwingingBackState,
    SwingingToDropState,
    WaitingDropState,
)
from factory_engine.persistence import (
    PersistenceError,
    load_world_from_file,
    save_world_to_file,
    world_from_dict,
    world_from_json,
    world_to_dict,
    world_to_json,
)
from factory_engine.power import PowerSystem
from factory_engine.views import (
    BeltItemView,
    DepositView,
    EntityView,
    WorldSnapshot,
    build_entity_view,
    build_world_snapshot,
    query_belt_item_views,
    query_deposit_views,
    query_entity_views,
)
from factory_engine.world import World


def step_inserter(
    state: InserterFSMState,
    satisfaction: int,
    swing_ticks: int,
    try_take: Callable[[], HeldItem | None],
    try_drop: Callable[[HeldItem], bool],
) -> InserterFSMState:
    """Advance an inserter FSM by one tick given satisfaction (0..1000) and I/O callbacks."""
    if satisfaction <= 0:
        return state

    swing_needed = ticks_to_progress(swing_ticks)

    if isinstance(state, IdleState):
        held = try_take()
        return SwingingToDropState(held=held, progress=0) if held is not None else state

    if isinstance(state, SwingingToDropState):
        new_progress = state.progress + satisfaction
        if new_progress < swing_needed:
            return SwingingToDropState(held=state.held, progress=new_progress)
        if try_drop(state.held):
            return SwingingBackState(progress=0)
        return WaitingDropState(held=state.held)

    if isinstance(state, WaitingDropState):
        if try_drop(state.held):
            return SwingingBackState(progress=0)
        return state

    new_progress = state.progress + satisfaction
    if new_progress >= swing_needed:
        return IdleState()
    return SwingingBackState(progress=new_progress)


class FactoryEngine:
    """Headless deterministic factory simulation engine."""

    def __init__(self, width: int, height: int) -> None:
        self._world: World = World(width, height)
        self._belt_system: BeltSystem = BeltSystem()
        self._power_system: PowerSystem = PowerSystem()

    @classmethod
    def _from_loaded_world(cls, world: World) -> "FactoryEngine":
        engine = cls.__new__(cls)
        engine._world = world
        engine._belt_system = BeltSystem()
        engine._power_system = PowerSystem()
        try:
            engine.validate_state()
        except ValueError as exc:
            raise PersistenceError(f"Loaded world failed invariant check: {exc}") from exc
        return engine

    def _sync_systems(self) -> None:
        if self._belt_system._built_version != self._world.topology_version:
            self._belt_system.rebuild_topology(self._world)
        self._power_system.update(self._world)

    def place_entity(
        self,
        entity_type: str | EntityType,
        x: int,
        y: int,
        direction: str | Direction | None = None,
        *,
        tier: int = 1,
    ) -> bool:
        """Place an entity with top-left corner at (x, y). Returns False if blocked/out of bounds."""
        placed = self._world.place_entity(entity_type, x, y, direction, tier=tier)
        return placed is not None

    def remove_entity(self, x: int, y: int) -> bool:
        """Remove the entity covering cell (x, y) (destroys its contents). Returns False if empty."""
        removed = self._world.remove_entity_at(x, y)
        return removed is not None

    def add_resource_patch(
        self,
        x: int,
        y: int,
        w: int,
        h: int,
        resource: str,
        amount: int,
    ) -> None:
        """Add or replace ore deposits across rectangle (x, y, w, h); raises ValueError if invalid."""
        self._world.add_resource_patch(x, y, w, h, resource, amount)

    def set_recipe(self, x: int, y: int, recipe_id: str | None) -> bool:
        """Assign recipe_id (or None) to the assembler covering (x, y); resets progress on change."""
        cx = require_strict_int(x, "x")
        cy = require_strict_int(y, "y")
        if recipe_id is not None:
            get_recipe(recipe_id)
        entity = self._world.get_entity_at(cx, cy)
        if not isinstance(entity, Assembler):
            return False
        entity.set_recipe(recipe_id)
        return True

    def insert_into_chest(
        self,
        x: int,
        y: int,
        item_type: str,
        count: int = 1,
    ) -> bool:
        """Insert count items into the chest at (x, y). Returns False if no chest or full."""
        cx = require_strict_int(x, "x")
        cy = require_strict_int(y, "y")
        validated_name = validate_item_name(item_type)
        qty = require_strict_int(count, "count")
        if qty <= 0:
            raise ValueError(f"Count must be positive, got {qty}")
        entity = self._world.get_entity_at(cx, cy)
        if not isinstance(entity, Chest):
            return False
        return entity.add_item(validated_name, qty)

    def drop_item_on_belt(
        self,
        x: int,
        y: int,
        item_type: str,
        lane: LaneSide = LaneSide.RIGHT,
    ) -> bool:
        """Drop one item at DROP_POS on the specified belt lane. Returns False if blocked or no belt."""
        cx = require_strict_int(x, "x")
        cy = require_strict_int(y, "y")
        validated_name = validate_item_name(item_type)
        side = parse_lane_side(lane)
        belt = self._world.get_belt_at(cx, cy)
        if belt is None or not belt.can_drop_item(lane_side=side, pos=DROP_POS):
            return False
        uid = self._world.allocate_item_uid()
        return belt.try_drop_item(
            uid=uid, item_type=validated_name, lane_side=side, pos=DROP_POS
        )

    def tick(self, steps: int = 1) -> int:
        """Advance simulation by steps ticks and return the resulting world tick counter.

        Not transactional: an unexpected internal error mid-tick may leave phases partially advanced.
        """
        count = require_strict_int(steps, "steps")
        if count < 0:
            raise ValueError(f"Tick steps cannot be negative: {count}")
        for _ in range(count):
            self._step_once()
        return self._world.tick

    def _step_once(self) -> None:
        self._power_system.update(self._world)
        self._update_miners()
        self._update_assemblers()
        self._belt_system.update_all(self._world)
        self._update_inserters()
        self._world.tick += 1

    def _update_miners(self) -> None:
        needed = ticks_to_progress(MINER_TIME_TICKS)
        for miner in self._world.get_entities_of_class(EntityType.MINER, Miner):
            satisfaction = self._power_system.get_satisfaction_for_entity(miner)
            if satisfaction <= 0 or not miner.can_produce():
                continue

            deposits = self._world.get_deposits_under_entity(miner)
            if not deposits:
                continue

            miner.progress += satisfaction
            if miner.progress >= needed:
                deposit = deposits[0]
                deposit.extract_one()
                miner.add_output(deposit.item_type, 1)
                self._world.cleanup_depleted_deposit(deposit.x, deposit.y)
                miner.progress -= needed

    def _update_assemblers(self) -> None:
        for assembler in self._world.get_entities_of_class(EntityType.ASSEMBLER, Assembler):
            satisfaction = self._power_system.get_satisfaction_for_entity(assembler)
            if satisfaction <= 0:
                continue

            recipe = assembler.get_current_recipe()
            if recipe is None or not assembler.can_craft():
                continue

            assembler.progress += satisfaction
            needed = recipe.total_progress()
            if assembler.progress >= needed:
                assembler.finish_craft_cycle()
                assembler.progress -= needed

    def _update_inserters(self) -> None:
        for inserter in self._world.get_entities_of_class(EntityType.INSERTER, Inserter):
            satisfaction = self._power_system.get_satisfaction_for_entity(inserter)
            if satisfaction <= 0:
                continue
            inserter.fsm_state = step_inserter(
                inserter.fsm_state,
                satisfaction,
                inserter.swing_ticks,
                lambda ins=inserter: self._try_inserter_pickup(ins),
                lambda held, ins=inserter: self._try_inserter_drop(ins, held),
            )

    def _can_target_accept_pickup(self, target: Entity, item_type: str) -> bool:
        if isinstance(target, Belt):
            return True
        if isinstance(target, Chest):
            return target.can_add_item(item_type, 1)
        if isinstance(target, Assembler):
            return target.can_accept_input(item_type, 1)
        return False

    def _try_inserter_pickup(self, inserter: Inserter) -> HeldItem | None:
        source = self._world.get_entity_at(*inserter.pickup_cell())
        target = self._world.get_entity_at(*inserter.drop_cell())
        if source is None or target is None:
            return None

        accepts = lambda item_type: self._can_target_accept_pickup(target, item_type)

        if isinstance(source, Belt):
            taken = source.try_take_item(accepts)
            if taken is None:
                return None
            return HeldItem(uid=taken.uid, item_type=taken.item_type)

        taken_type: str | None = None
        if isinstance(source, (Miner, Assembler)):
            taken_type = source.take_output(accepts)
        elif isinstance(source, Chest):
            taken_type = source.take_item(accepts)

        if taken_type is None:
            return None
        return HeldItem(uid=self._world.allocate_item_uid(), item_type=taken_type)

    def _try_inserter_drop(self, inserter: Inserter, held: HeldItem) -> bool:
        target = self._world.get_entity_at(*inserter.drop_cell())
        if target is None:
            return False

        if isinstance(target, Belt):
            return target.try_drop_item(
                uid=held.uid,
                item_type=held.item_type,
                lane_side=LaneSide.RIGHT,
                pos=DROP_POS,
            )
        if isinstance(target, Chest):
            return target.add_item(held.item_type, 1)
        if isinstance(target, Assembler):
            return target.add_input(held.item_type, 1)
        return False

    def get_tick(self) -> int:
        """Return the current simulation tick counter."""
        return self._world.tick

    def get_entity_at(self, x: int, y: int) -> EntityView | None:
        """Return the EntityView covering cell (x, y) (any cell of multi-tile entities), or None."""
        cx = require_strict_int(x, "x")
        cy = require_strict_int(y, "y")
        self._sync_systems()
        entity = self._world.get_entity_at(cx, cy)
        if entity is None:
            return None
        return build_entity_view(entity, self._power_system)

    def get_entities(
        self,
        entity_type: str | EntityType | None = None,
        rect: Rect | tuple[int, int, int, int] | None = None,
    ) -> tuple[EntityView, ...]:
        """Return sorted EntityViews filtered by optional entity_type and overlapping rect."""
        self._sync_systems()
        return query_entity_views(
            self._world,
            power_system=self._power_system,
            entity_type=entity_type,
            rect=rect,
        )

    def get_belt_items(
        self,
        rect: Rect | tuple[int, int, int, int] | None = None,
    ) -> tuple[BeltItemView, ...]:
        """Return all belt items (optionally within overlapping rect) with world coordinates."""
        self._sync_systems()
        return query_belt_item_views(self._world, self._belt_system, rect=rect)

    def get_deposits(
        self,
        rect: Rect | tuple[int, int, int, int] | None = None,
    ) -> tuple[DepositView, ...]:
        """Return active ore deposits sorted by (x, y), optionally overlapping rect."""
        return query_deposit_views(self._world, rect=rect)

    def get_power_summary(self) -> dict[str, int]:
        """Return aggregate power network metrics (counts and total production/consumption kW)."""
        self._sync_systems()
        return self._power_system.get_summary()

    def snapshot(
        self,
        rect: Rect | tuple[int, int, int, int] | None = None,
    ) -> WorldSnapshot:
        """Return an immutable WorldSnapshot (entities, belt items, deposits, networks, wires)."""
        self._sync_systems()
        return build_world_snapshot(
            self._world,
            self._belt_system,
            self._power_system,
            rect=rect,
        )

    def count_all_items(self) -> dict[str, int]:
        """Count all items on belts, in inserter hands, machine buffers, and chests (excludes deposits)."""
        totals = self._belt_system.count_items_by_type(self._world)

        for entity in self._world.get_all_entities():
            if isinstance(entity, Inserter) and entity.held is not None:
                name = entity.held.item_type
                totals[name] = totals.get(name, 0) + 1
            elif isinstance(entity, Miner):
                for item_type, count in entity.output_buffer.items_sorted():
                    totals[item_type] = totals.get(item_type, 0) + count
            elif isinstance(entity, Assembler):
                for item_type, count in entity.input_buffer.items_sorted():
                    totals[item_type] = totals.get(item_type, 0) + count
                for item_type, count in entity.output_buffer.items_sorted():
                    totals[item_type] = totals.get(item_type, 0) + count
            elif isinstance(entity, Chest):
                for item_type, count in entity.inventory.items_sorted():
                    totals[item_type] = totals.get(item_type, 0) + count

        return dict(sorted(totals.items()))

    def validate_state(self) -> None:
        """Verify all world, belt, power, buffer, and UID invariants; raises ValueError on violation."""
        self._sync_systems()
        self._world.validate_spatial_index()
        self._belt_system.validate_all_belts(self._world)
        self._power_system.validate_networks(self._world)

        seen_uids: set[int] = set()
        for belt in self._world.get_entities_of_class(EntityType.BELT, Belt):
            for lane in belt.lanes:
                for item in lane.items:
                    self._check_item_uid(item.uid, seen_uids)

        for inserter in self._world.get_entities_of_class(EntityType.INSERTER, Inserter):
            if inserter.held is not None:
                self._check_item_uid(inserter.held.uid, seen_uids)

        for miner in self._world.get_entities_of_class(EntityType.MINER, Miner):
            if miner.progress < 0:
                raise ValueError(f"Negative miner progress at {(miner.x, miner.y)}")
            miner.output_buffer.validate()

        for assembler in self._world.get_entities_of_class(EntityType.ASSEMBLER, Assembler):
            if assembler.progress < 0:
                raise ValueError(f"Negative assembler progress at {(assembler.x, assembler.y)}")
            if assembler.recipe_id is None:
                if assembler.progress != 0:
                    raise ValueError(
                        f"Assembler without recipe has non-zero progress at {(assembler.x, assembler.y)}"
                    )
            else:
                get_recipe(assembler.recipe_id)
            assembler.input_buffer.validate()
            assembler.output_buffer.validate()

        for chest in self._world.get_entities_of_class(EntityType.CHEST, Chest):
            chest.inventory.validate()

    def _check_item_uid(self, uid: int, seen_uids: set[int]) -> None:
        if uid <= 0 or uid >= self._world.next_item_uid:
            raise ValueError(
                f"Item uid {uid} out of range 1..{self._world.next_item_uid - 1}"
            )
        if uid in seen_uids:
            raise ValueError(f"Duplicate live item uid: {uid}")
        seen_uids.add(uid)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the world state into a fresh canonical dictionary."""
        return world_to_dict(self._world)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FactoryEngine":
        """Deserialize and validate a world dictionary; raises PersistenceError on any failure."""
        world = world_from_dict(data)
        return cls._from_loaded_world(world)

    def to_json(self) -> str:
        """Serialize the world state into a canonical JSON string."""
        return world_to_json(self._world)

    @classmethod
    def from_json(cls, raw_json: str) -> "FactoryEngine":
        """Parse and validate a JSON string; raises PersistenceError on any failure."""
        world = world_from_json(raw_json)
        return cls._from_loaded_world(world)

    def save(self, path: str | Path) -> None:
        """Atomically write canonical JSON state to path; raises PersistenceError on I/O failure."""
        save_world_to_file(self._world, path)

    @classmethod
    def load(cls, path: str | Path) -> "FactoryEngine":
        """Read, decode, parse, and validate a save file; raises PersistenceError on any failure."""
        world = load_world_from_file(path)
        return cls._from_loaded_world(world)