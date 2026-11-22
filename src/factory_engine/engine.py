from pathlib import Path

from factory_engine.belts import BeltSystem
from factory_engine.constants import (
    DROP_POS,
    INSERTER_SWING_TICKS,
    LEFT_LANE,
    MINER_TIME_TICKS,
    RIGHT_LANE,
    SAT_ONE,
    Direction,
    InserterState,
)
from factory_engine.entities import (
    Assembler,
    Belt,
    Chest,
    Entity,
    Inserter,
    Miner,
)
from factory_engine.persistence import (
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


class FactoryEngine:
    def __init__(self, width: int, height: int) -> None:
        self.world: World = World(width, height)
        self.belt_system: BeltSystem = BeltSystem()
        self.power_system: PowerSystem = PowerSystem()

    @classmethod
    def from_world(cls, world: World) -> "FactoryEngine":
        engine = cls(world.width, world.height)
        engine.world = world
        engine._sync_systems()
        return engine

    def _sync_systems(self) -> None:
        if self.world.belts_dirty:
            self.belt_system.rebuild_topology(self.world)
        if self.world.power_dirty:
            self.power_system.rebuild_networks(self.world)

    def place_entity(
        self,
        entity_type: str,
        x: int,
        y: int,
        direction: str | Direction | None = None,
    ) -> bool:
        placed = self.world.place_entity(entity_type, x, y, direction)
        return placed is not None

    def remove_entity(self, x: int, y: int) -> bool:
        removed = self.world.remove_entity_at(x, y)
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
        self.world.add_resource_patch(x, y, w, h, resource, amount)

    def set_recipe(self, x: int, y: int, recipe_id: str | None) -> bool:
        entity = self.world.get_entity_at(x, y)
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
        entity = self.world.get_entity_at(x, y)
        if not isinstance(entity, Chest):
            return False
        return entity.add_item(item_type, count)

    def drop_item_on_belt(
        self,
        x: int,
        y: int,
        item_type: str,
        lane: int = RIGHT_LANE,
    ) -> bool:
        belt = self.world.get_belt_at(x, y)
        if belt is None:
            return False
        if not belt.can_drop_item(lane_index=lane, pos=DROP_POS):
            return False
        uid = self.world.allocate_item_uid()
        return belt.try_drop_item(
            uid=uid, item_type=item_type, lane_index=lane, pos=DROP_POS
            )

    def tick(self, steps: int = 1) -> int:
        if steps < 0:
            raise ValueError(f"Tick steps cannot be negative: {steps}")
        for _ in range(steps):
            self._step_once()
        return self.world.tick

    def _step_once(self) -> None:
        self.power_system.update(self.world)
        self._update_miners()
        self._update_assemblers()
        self.belt_system.update_all(self.world)
        self._update_inserters()
        self.world.tick += 1

    def _update_miners(self) -> None:
        needed = MINER_TIME_TICKS * SAT_ONE
        for entity in self.world.get_entities_of_type("miner"):
            if not isinstance(entity, Miner):
                continue
            sat = self.power_system.get_satisfaction_for_entity(entity)
            if sat <= 0:
                continue
            if not entity.can_produce():
                continue

            deposits = self.world.get_deposits_under_entity(entity)
            if not deposits:
                continue

            entity.progress += sat
            if entity.progress >= needed:
                deposit = deposits[0]
                taken = deposit.extract(1)
                if taken > 0:
                    entity.add_output(deposit.resource_type, taken)
                    self.world.cleanup_depleted_resource(deposit.x, deposit.y)
                    entity.progress -= needed

    def _update_assemblers(self) -> None:
        for entity in self.world.get_entities_of_type("assembler"):
            if not isinstance(entity, Assembler):
                continue
            sat = self.power_system.get_satisfaction_for_entity(entity)
            if sat <= 0:
                continue

            recipe = entity.get_current_recipe()
            if recipe is None:
                continue
            if not entity.can_craft():
                continue

            entity.progress += sat
            needed = recipe.total_progress()
            if entity.progress >= needed:
                if entity.finish_craft_cycle():
                    entity.progress -= needed

    def _update_inserters(self) -> None:
        swing_needed = INSERTER_SWING_TICKS * SAT_ONE
        for entity in self.world.get_entities_of_type("inserter"):
            if not isinstance(entity, Inserter):
                continue
            sat = self.power_system.get_satisfaction_for_entity(entity)
            if sat <= 0:
                continue
            self._update_single_inserter(entity, sat, swing_needed)

    def _update_single_inserter(
        self,
        inserter: Inserter,
        sat: int,
        swing_needed: int,
    ) -> None:
        if inserter.state == InserterState.IDLE:
            if self._try_inserter_pickup(inserter):
                inserter.state = InserterState.SWING_TO_DROP
                inserter.progress = 0
            return

        if inserter.state == InserterState.SWING_TO_DROP:
            inserter.progress += sat
            if inserter.progress >= swing_needed:
                inserter.progress = 0
                if self._try_inserter_drop(inserter):
                    inserter.state = InserterState.SWING_BACK
                else:
                    inserter.state = InserterState.WAIT_DROP
            return

        if inserter.state == InserterState.WAIT_DROP:
            if self._try_inserter_drop(inserter):
                inserter.state = InserterState.SWING_BACK
                inserter.progress = 0
            return

        if inserter.state == InserterState.SWING_BACK:
            inserter.progress += sat
            if inserter.progress >= swing_needed:
                inserter.progress = 0
                inserter.state = InserterState.IDLE

    def _can_target_accept_pickup(self, target: Entity | None, item_type: str) -> bool:
        if target is None:
            return False
        if isinstance(target, Belt):
            return True
        if isinstance(target, Chest):
            return target.can_add_item(1)
        if isinstance(target, Assembler):
            return target.can_accept_input(item_type)
        return False

    def _take_matching_from_buffer(
        self,
        buffer: dict[str, int],
        target: Entity,
    ) -> str | None:
        for item_type in sorted(buffer.keys()):
            if buffer[item_type] <= 0:
                continue
            if self._can_target_accept_pickup(target, item_type):
                buffer[item_type] -= 1
                if buffer[item_type] == 0:
                    del buffer[item_type]
                return item_type
        return None

    def _try_inserter_pickup(self, inserter: Inserter) -> bool:
        px, py = inserter.pickup_cell()
        dx, dy = inserter.drop_cell()
        source = self.world.get_entity_at(px, py)
        target = self.world.get_entity_at(dx, dy)

        if source is None or target is None:
            return False

        if isinstance(source, Belt):
            for lane_idx in (LEFT_LANE, RIGHT_LANE):
                lane = source.get_lane(lane_idx)
                front = lane.front_item()
                if front is None:
                    continue
                if self._can_target_accept_pickup(target, front.item_type):
                    taken = lane.take_front_item()
                    assert taken is not None
                    inserter.held_item = taken.item_type
                    inserter.held_uid = taken.uid
                    return True
            return False

        taken_type: str | None = None
        if isinstance(source, Miner):
            taken_type = self._take_matching_from_buffer(source.output_buffer, target)
        elif isinstance(source, Assembler):
            taken_type = self._take_matching_from_buffer(source.output_buffer, target)
        elif isinstance(source, Chest):
            taken_type = self._take_matching_from_buffer(source.inventory, target)

        if taken_type is None:
            return False

        inserter.held_item = taken_type
        inserter.held_uid = self.world.allocate_item_uid()
        return True

    def _try_inserter_drop(self, inserter: Inserter) -> bool:
        if inserter.held_item is None or inserter.held_uid is None:
            return False

        dx, dy = inserter.drop_cell()
        target = self.world.get_entity_at(dx, dy)
        if target is None:
            return False

        dropped = False
        if isinstance(target, Belt):
            dropped = target.try_drop_item(
                uid=inserter.held_uid,
                item_type=inserter.held_item,
                lane_index=RIGHT_LANE,
                pos=DROP_POS,
            )
        elif isinstance(target, Chest):
            dropped = target.add_item(inserter.held_item, 1)
        elif isinstance(target, Assembler):
            dropped = target.add_input(inserter.held_item, 1)

        if dropped:
            inserter.held_item = None
            inserter.held_uid = None
            return True

        return False

    def get_tick(self) -> int:
        return self.world.tick

    def get_entity_at(self, x: int, y: int) -> EntityView | None:
        self._sync_systems()
        entity = self.world.get_entity_at(x, y)
        if entity is None:
            return None
        return build_entity_view(entity, self.power_system)

    def get_entities(
        self,
        entity_type: str | None = None,
        rect: tuple[int, int, int, int] | None = None,
    ) -> tuple[EntityView, ...]:
        self._sync_systems()
        return query_entity_views(
            self.world,
            power_system=self.power_system,
            entity_type=entity_type,
            rect=rect,
        )

    def get_belt_items(
        self,
        rect: tuple[int, int, int, int] | None = None,
    ) -> tuple[BeltItemView, ...]:
        self._sync_systems()
        return query_belt_item_views(self.world, self.belt_system, rect=rect)

    def get_deposits(
        self,
        rect: tuple[int, int, int, int] | None = None,
    ) -> tuple[DepositView, ...]:
        return query_deposit_views(self.world, rect=rect)

    def get_power_summary(self) -> dict[str, int]:
        self._sync_systems()
        return self.power_system.get_summary()

    def snapshot(
        self,
        rect: tuple[int, int, int, int] | None = None,
    ) -> WorldSnapshot:
        self._sync_systems()
        return build_world_snapshot(
            self.world,
            self.belt_system,
            self.power_system,
            rect=rect,
        )

    def count_all_items(self) -> dict[str, int]:
        totals = self.belt_system.count_items_by_type(self.world)

        for entity in self.world.get_all_entities():
            if isinstance(entity, Inserter) and entity.held_item is not None:
                totals[entity.held_item] = totals.get(entity.held_item, 0) + 1
            elif isinstance(entity, Miner):
                for item_type, count in entity.output_buffer.items():
                    totals[item_type] = totals.get(item_type, 0) + count
            elif isinstance(entity, Assembler):
                for item_type, count in entity.input_buffer.items():
                    totals[item_type] = totals.get(item_type, 0) + count
                for item_type, count in entity.output_buffer.items():
                    totals[item_type] = totals.get(item_type, 0) + count
            elif isinstance(entity, Chest):
                for item_type, count in entity.inventory.items():
                    totals[item_type] = totals.get(item_type, 0) + count

        return dict(sorted(totals.items()))

    def validate_state(self) -> None:
        self._sync_systems()
        self.world.validate_spatial_index()
        self.belt_system.validate_all_belts(self.world)
        self.power_system.validate_networks(self.world)

    def to_dict(self) -> dict:
        return world_to_dict(self.world)

    @classmethod
    def from_dict(cls, data: dict) -> "FactoryEngine":
        world = world_from_dict(data)
        return cls.from_world(world)

    def to_json(self) -> str:
        return world_to_json(self.world)

    @classmethod
    def from_json(cls, raw_json: str) -> "FactoryEngine":
        world = world_from_json(raw_json)
        return cls.from_world(world)

    def save(self, path: str | Path) -> None:
        save_world_to_file(self.world, path)

    @classmethod
    def load(cls, path: str | Path) -> "FactoryEngine":
        world = load_world_from_file(path)
        return cls.from_world(world)