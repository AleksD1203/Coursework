from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from factory_engine.constants import (
    ASSEMBLER_INPUT_LIMIT_PER_ITEM,
    ASSEMBLER_OUTPUT_TOTAL_LIMIT,
    CHEST_ITEM_LIMIT,
    DROP_POS,
    ITEM_SPACING,
    MAX_POS,
    MINER_OUTPUT_TOTAL_LIMIT,
    POLE_SUPPLY_RADIUS,
    POLE_WIRE_REACH,
    Direction,
    EntityType,
    InserterState,
    LaneSide,
    Recipe,
    get_belt_speed,
    get_entity_size,
    get_inserter_swing_ticks,
    get_power_consumption_kw,
    get_power_production_kw,
    get_recipe,
    needs_direction,
    parse_direction,
    parse_entity_type,
    parse_lane_side,
    require_strict_int,
    supports_tier,
    validate_item_name,
    validate_tier,
)


class ItemBuffer:
    def __init__(
        self,
        *,
        max_total: int | None = None,
        max_per_item: int | None = None,
    ) -> None:
        self._counts: dict[str, int] = {}
        self._max_total: int | None = max_total
        self._max_per_item: int | None = max_per_item

    def total(self) -> int:
        """Return total number of items stored across all item types."""
        return sum(self._counts.values())

    def is_empty(self) -> bool:
        """Return True when the buffer stores no items."""
        return not self._counts

    def get(self, item_type: str) -> int:
        """Return stored count of item_type (0 if absent)."""
        return self._counts.get(item_type, 0)

    def can_add(self, item_type: str, count: int = 1) -> bool:
        """Return True if adding count units of item_type respects capacity limits."""
        if self._max_total is not None and self.total() + count > self._max_total:
            return False
        if (
            self._max_per_item is not None
            and self._counts.get(item_type, 0) + count > self._max_per_item
        ):
            return False
        return True

    def add(self, item_type: str, count: int = 1) -> bool:
        """Validate arguments and add count units if capacity allows."""
        name = validate_item_name(item_type)
        amount = require_strict_int(count, "count")
        if amount <= 0:
            raise ValueError(f"Count must be positive, got {amount}")
        if not self.can_add(name, amount):
            return False
        self._counts[name] = self._counts.get(name, 0) + amount
        return True

    def take_matching(
        self, predicate: Callable[[str], bool] | None = None
    ) -> str | None:
        """Remove and return 1 unit of the first sorted item accepted by predicate."""
        for item_type in sorted(self._counts):
            if predicate is None or predicate(item_type):
                remaining = self._counts[item_type] - 1
                if remaining == 0:
                    del self._counts[item_type]
                else:
                    self._counts[item_type] = remaining
                return item_type
        return None

    def has_all(self, requirements: Mapping[str, int]) -> bool:
        """Return True if the buffer contains at least the required counts."""
        return all(
            self._counts.get(item_type, 0) >= needed
            for item_type, needed in requirements.items()
        )

    def consume_many(self, requirements: Mapping[str, int]) -> None:
        if not self.has_all(requirements):
            raise ValueError("Insufficient items in buffer to consume recipe inputs")
        for item_type, needed in requirements.items():
            remaining = self._counts[item_type] - needed
            if remaining == 0:
                del self._counts[item_type]
            else:
                self._counts[item_type] = remaining

    def items_sorted(self) -> tuple[tuple[str, int], ...]:
        """Return an immutable sorted tuple of (item_type, count) pairs."""
        return tuple(sorted(self._counts.items()))

    def as_dict(self) -> dict[str, int]:
        """Return a fresh sorted dictionary copy of stored counts."""
        return dict(sorted(self._counts.items()))

    def load_counts(self, counts: Mapping[str, int]) -> None:
        """Validate and atomically replace buffer contents from deserialized data."""
        loaded: dict[str, int] = {}
        running_total = 0
        for raw_name, raw_count in counts.items():
            name = validate_item_name(raw_name)
            amount = require_strict_int(raw_count, f"buffer[{name!r}]")
            if amount <= 0:
                raise ValueError(f"Buffer count for {name!r} must be positive: {amount}")
            if self._max_per_item is not None and amount > self._max_per_item:
                raise ValueError(
                    f"Buffer count for {name!r} exceeds per-item limit {self._max_per_item}: {amount}"
                )
            running_total += amount
            loaded[name] = amount

        if self._max_total is not None and running_total > self._max_total:
            raise ValueError(
                f"Buffer total {running_total} exceeds limit {self._max_total}"
            )
        self._counts = loaded

    def validate(self) -> None:
        running_total = 0
        for name, amount in self._counts.items():
            validate_item_name(name)
            require_strict_int(amount, f"buffer[{name!r}]")
            if amount <= 0:
                raise ValueError(f"Non-positive count in buffer for {name!r}: {amount}")
            if self._max_per_item is not None and amount > self._max_per_item:
                raise ValueError(
                    f"Per-item limit exceeded for {name!r}: {amount} > {self._max_per_item}"
                )
            running_total += amount
        if self._max_total is not None and running_total > self._max_total:
            raise ValueError(
                f"Total buffer limit exceeded: {running_total} > {self._max_total}"
            )


@dataclass
class Deposit:
    x: int
    y: int
    item_type: str
    amount: int

    def __post_init__(self) -> None:
        require_strict_int(self.x, "deposit.x")
        require_strict_int(self.y, "deposit.y")
        validate_item_name(self.item_type)
        amount = require_strict_int(self.amount, "deposit.amount")
        if amount <= 0:
            raise ValueError(f"Deposit amount must be positive: {amount}")

    def is_depleted(self) -> bool:
        return self.amount == 0

    def extract_one(self) -> bool:
        """Extract 1 unit of ore if available."""
        if self.amount <= 0:
            return False
        self.amount -= 1
        return True


@dataclass(frozen=True, slots=True)
class BeltItem:
    uid: int
    item_type: str
    pos: int

    def __post_init__(self) -> None:
        uid = require_strict_int(self.uid, "item.uid")
        if uid <= 0:
            raise ValueError(f"Item uid must be positive: {uid}")
        validate_item_name(self.item_type)
        pos = require_strict_int(self.pos, "item.pos")
        if pos < 0 or pos > MAX_POS:
            raise ValueError(f"Item pos out of range 0..{MAX_POS}: {pos}")


def _validate_sorted_lane_items(items: Sequence[BeltItem]) -> None:
    for i in range(1, len(items)):
        prev_item = items[i - 1]
        cur_item = items[i]
        if prev_item.pos < cur_item.pos:
            raise ValueError("Lane items are not sorted descending by pos")
        if prev_item.pos - cur_item.pos < ITEM_SPACING:
            raise ValueError(
                f"Lane spacing violated: {prev_item.pos} and {cur_item.pos}"
            )


class Lane:
    def __init__(self, items: Sequence[BeltItem] | None = None) -> None:
        self._items: list[BeltItem] = []
        if items is not None:
            sorted_items = sorted(items, key=lambda it: it.pos, reverse=True)
            _validate_sorted_lane_items(sorted_items)
            self._items = sorted_items

    @property
    def items(self) -> tuple[BeltItem, ...]:
        """Read-only snapshot of items ordered from front (highest pos) to back."""
        return tuple(self._items)

    def is_empty(self) -> bool:
        return not self._items

    def count(self) -> int:
        return len(self._items)

    def front_item(self) -> BeltItem | None:
        return self._items[0] if self._items else None

    def back_item(self) -> BeltItem | None:
        return self._items[-1] if self._items else None

    def can_insert_at(self, pos: int) -> bool:
        pos_val = require_strict_int(pos, "pos")
        if pos_val < 0 or pos_val > MAX_POS:
            return False
        return all(abs(existing.pos - pos_val) >= ITEM_SPACING for existing in self._items)

    def insert_item(self, item: BeltItem) -> bool:
        if not self.can_insert_at(item.pos):
            return False
        updated = [*self._items, item]
        updated.sort(key=lambda it: it.pos, reverse=True)
        self._items = updated
        return True

    def replace_items(self, items: Sequence[BeltItem]) -> None:
        """Atomically replace lane contents after a belt tick, enforcing invariants."""
        new_items = list(items)
        _validate_sorted_lane_items(new_items)
        self._items = new_items

    def append_back_items(self, items: Sequence[BeltItem]) -> None:
        """Atomically append handed-off items behind the back item, enforcing invariants."""
        if not items:
            return
        combined = [*self._items, *items]
        _validate_sorted_lane_items(combined)
        self._items = combined

    def take_front_item(self) -> BeltItem | None:
        """Remove and return the front-most item on the lane, or None if empty."""
        if not self._items:
            return None
        return self._items.pop(0)

    def validate_invariants(self) -> None:
        _validate_sorted_lane_items(self._items)


@dataclass(frozen=True, slots=True)
class HeldItem:
    uid: int
    item_type: str

    def __post_init__(self) -> None:
        uid = require_strict_int(self.uid, "held_item.uid")
        if uid <= 0:
            raise ValueError(f"HeldItem uid must be positive: {uid}")
        validate_item_name(self.item_type)


@dataclass(frozen=True, slots=True)
class IdleState:
    @property
    def label(self) -> InserterState:
        return InserterState.IDLE

    @property
    def held(self) -> HeldItem | None:
        return None

    @property
    def progress(self) -> int:
        return 0


@dataclass(frozen=True, slots=True)
class SwingingToDropState:
    held: HeldItem
    progress: int = 0  # thousandths of a full-power tick

    def __post_init__(self) -> None:
        prog = require_strict_int(self.progress, "inserter.progress")
        if prog < 0:
            raise ValueError(f"Inserter progress cannot be negative: {prog}")

    @property
    def label(self) -> InserterState:
        return InserterState.SWING_TO_DROP


@dataclass(frozen=True, slots=True)
class WaitingDropState:
    held: HeldItem

    @property
    def label(self) -> InserterState:
        return InserterState.WAIT_DROP

    @property
    def progress(self) -> int:
        return 0


@dataclass(frozen=True, slots=True)
class SwingingBackState:
    progress: int = 0  # thousandths of a full-power tick

    def __post_init__(self) -> None:
        prog = require_strict_int(self.progress, "inserter.progress")
        if prog < 0:
            raise ValueError(f"Inserter progress cannot be negative: {prog}")

    @property
    def label(self) -> InserterState:
        return InserterState.SWING_BACK

    @property
    def held(self) -> HeldItem | None:
        return None


InserterFSMState = (
    IdleState | SwingingToDropState | WaitingDropState | SwingingBackState
)


def build_inserter_state(
    label: InserterState,
    held: HeldItem | None,
    progress: int,
) -> InserterFSMState:
    """Build a validated inserter FSM state from serialized fields."""
    prog = require_strict_int(progress, "inserter.progress")
    if label is InserterState.IDLE:
        if held is not None or prog != 0:
            raise ValueError("IDLE inserter must have no held item and progress == 0")
        return IdleState()
    if label is InserterState.SWING_TO_DROP:
        if held is None:
            raise ValueError("SWING_TO_DROP inserter must hold an item")
        return SwingingToDropState(held=held, progress=prog)
    if label is InserterState.WAIT_DROP:
        if held is None or prog != 0:
            raise ValueError("WAIT_DROP inserter must hold an item and have progress == 0")
        return WaitingDropState(held=held)
    if label is InserterState.SWING_BACK:
        if held is not None:
            raise ValueError("SWING_BACK inserter cannot hold an item")
        return SwingingBackState(progress=prog)
    raise ValueError(f"Unknown inserter state: {label!r}")


@dataclass(frozen=True, slots=True)
class DeadEndLink:
    """Outgoing belt link with no target belt."""


@dataclass(frozen=True, slots=True)
class StraightLink:
    """Outgoing straight or curved link to another belt (lanes map 1:1)."""

    target: "Belt"


@dataclass(frozen=True, slots=True)
class SideLoadLink:
    """Outgoing side-loading link into one specific lane of a target belt."""

    target: "Belt"
    target_side: LaneSide


BeltLink = DeadEndLink | StraightLink | SideLoadLink


class Entity:
    def __init__(
        self,
        entity_type: EntityType,
        x: int,
        y: int,
        direction: Direction | None = None,
        *,
        tier: int = 1,
    ) -> None:
        self.entity_type: EntityType = entity_type
        self.x: int = require_strict_int(x, "x")
        self.y: int = require_strict_int(y, "y")
        self.tier: int = validate_tier(tier)
        width, height = get_entity_size(entity_type, direction)
        self.width: int = width
        self.height: int = height
        self.direction: Direction | None = direction
        self.network_id: int | None = None

    def occupied_cells(self) -> list[tuple[int, int]]:
        """Return all grid coordinates covered by this entity."""
        return [
            (self.x + dx, self.y + dy)
            for dy in range(self.height)
            for dx in range(self.width)
        ]

    def power_consumption_kw(self) -> int:
        """Return active power demand in kW."""
        return get_power_consumption_kw(self.entity_type, self.tier)

    def power_production_kw(self) -> int:
        """Return power generation capacity in kW."""
        return get_power_production_kw(self.entity_type)

    def sort_key(self) -> tuple[int, int]:
        return (self.x, self.y)


class DirectionalEntity(Entity):
    direction: Direction

    def __init__(
        self,
        entity_type: EntityType,
        x: int,
        y: int,
        direction: Direction,
        *,
        tier: int = 1,
    ) -> None:
        super().__init__(entity_type, x, y, direction, tier=tier)
        self.direction = direction


class Belt(DirectionalEntity):
    def __init__(
        self, x: int, y: int, direction: Direction, *, tier: int = 1
    ) -> None:
        super().__init__(EntityType.BELT, x, y, direction, tier=tier)
        self.lanes: tuple[Lane, Lane] = (Lane(), Lane())
        self.link: BeltLink = DeadEndLink()

    @property
    def speed(self) -> int:
        """Movement speed in integer units per tick for this belt's tier."""
        return get_belt_speed(self.tier)

    def get_lane(self, lane_side: LaneSide) -> Lane:
        """Return the Lane object for the specified LaneSide."""
        side = parse_lane_side(lane_side)
        return self.lanes[side.index]

    def next_cell(self) -> tuple[int, int]:
        """Return the grid cell immediately ahead in the direction of travel."""
        dx, dy = self.direction.delta()
        return (self.x + dx, self.y + dy)

    def item_count(self) -> int:
        """Return total number of items across both lanes."""
        return self.lanes[0].count() + self.lanes[1].count()

    def can_drop_item(
        self, lane_side: LaneSide = LaneSide.RIGHT, pos: int = DROP_POS
    ) -> bool:
        """Return True if an item can be placed at pos on the given lane without overlap."""
        return self.get_lane(lane_side).can_insert_at(pos)

    def try_drop_item(
        self,
        uid: int,
        item_type: str,
        lane_side: LaneSide = LaneSide.RIGHT,
        pos: int = DROP_POS,
    ) -> bool:
        """Validate arguments and drop a new item onto the given lane if space permits."""
        validated_name = validate_item_name(item_type)
        lane = self.get_lane(lane_side)
        # Check spacing before constructing BeltItem so out-of-range pos returns False cleanly.
        if not lane.can_insert_at(pos):
            return False
        return lane.insert_item(BeltItem(uid=uid, item_type=validated_name, pos=pos))

    def try_take_item(
        self, predicate: Callable[[str], bool] | None = None
    ) -> BeltItem | None:
        """Take the front item of LEFT lane, else RIGHT lane, matching predicate."""
        for lane in self.lanes:
            front = lane.front_item()
            if front is not None and (predicate is None or predicate(front.item_type)):
                return lane.take_front_item()
        return None


class Inserter(DirectionalEntity):
    """Single-tile robotic arm moving items between pickup_cell and drop_cell."""

    def __init__(
        self, x: int, y: int, direction: Direction, *, tier: int = 1
    ) -> None:
        super().__init__(EntityType.INSERTER, x, y, direction, tier=tier)
        self.fsm_state: InserterFSMState = IdleState()

    @property
    def swing_ticks(self) -> int:
        """Full-power ticks required for one half-swing at this inserter's tier."""
        return get_inserter_swing_ticks(self.tier)

    @property
    def state(self) -> InserterState:
        return self.fsm_state.label

    @property
    def held(self) -> HeldItem | None:
        return self.fsm_state.held

    @property
    def progress(self) -> int:
        return self.fsm_state.progress

    def pickup_cell(self) -> tuple[int, int]:
        """Return the grid cell behind the inserter from which it picks items."""
        dx, dy = self.direction.opposite().delta()
        return (self.x + dx, self.y + dy)

    def drop_cell(self) -> tuple[int, int]:
        """Return the grid cell ahead of the inserter onto which it drops items."""
        dx, dy = self.direction.delta()
        return (self.x + dx, self.y + dy)


class Miner(Entity):
    """2x2 mining drill extracting ore from underlying deposits."""

    def __init__(self, x: int, y: int) -> None:
        super().__init__(EntityType.MINER, x, y)
        self.output_buffer: ItemBuffer = ItemBuffer(max_total=MINER_OUTPUT_TOTAL_LIMIT)
        self.progress: int = 0  # thousandths of a full-power tick

    def can_produce(self) -> bool:
        return self.output_buffer.total() < MINER_OUTPUT_TOTAL_LIMIT

    def add_output(self, item_type: str, count: int = 1) -> bool:
        return self.output_buffer.add(item_type, count)

    def take_output(
        self, predicate: Callable[[str], bool] | None = None
    ) -> str | None:
        return self.output_buffer.take_matching(predicate)


class Assembler(Entity):
    """3x3 crafting machine executing a single selected recipe."""

    def __init__(self, x: int, y: int) -> None:
        super().__init__(EntityType.ASSEMBLER, x, y)
        self.recipe_id: str | None = None
        self.input_buffer: ItemBuffer = ItemBuffer(
            max_per_item=ASSEMBLER_INPUT_LIMIT_PER_ITEM
        )
        self.output_buffer: ItemBuffer = ItemBuffer(
            max_total=ASSEMBLER_OUTPUT_TOTAL_LIMIT
        )
        self.progress: int = 0  # thousandths of a full-power tick

    def set_recipe(self, recipe_id: str | None) -> None:
        """Select a recipe (or None) and reset crafting progress on any change."""
        if recipe_id is None:
            self.recipe_id = None
            self.progress = 0
            return
        get_recipe(recipe_id)
        if self.recipe_id != recipe_id:
            self.recipe_id = recipe_id
            self.progress = 0

    def get_current_recipe(self) -> Recipe | None:
        if self.recipe_id is None:
            return None
        return get_recipe(self.recipe_id)

    def can_accept_input(self, item_type: str, count: int = 1) -> bool:
        recipe = self.get_current_recipe()
        if recipe is None or item_type not in recipe.ingredients:
            return False
        return self.input_buffer.can_add(item_type, count)

    def add_input(self, item_type: str, count: int = 1) -> bool:
        validated_name = validate_item_name(item_type)
        amount = require_strict_int(count, "count")
        if amount <= 0:
            raise ValueError(f"Count must be positive, got {amount}")
        if not self.can_accept_input(validated_name, amount):
            return False
        return self.input_buffer.add(validated_name, amount)

    def can_craft(self) -> bool:
        recipe = self.get_current_recipe()
        if recipe is None:
            return False
        if not self.input_buffer.has_all(recipe.ingredients):
            return False
        produced_count = sum(recipe.results.values())
        return self.output_buffer.total() + produced_count <= ASSEMBLER_OUTPUT_TOTAL_LIMIT

    def finish_craft_cycle(self) -> Recipe:
        """Consume recipe inputs, add outputs, and return the completed recipe."""
        recipe = self.get_current_recipe()
        if recipe is None:
            raise ValueError("Cannot finish craft cycle without an active recipe")
        self.input_buffer.consume_many(recipe.ingredients)
        for item_type, produced in recipe.results.items():
            if not self.output_buffer.add(item_type, produced):
                raise ValueError("Assembler output buffer limit exceeded during craft")
        return recipe

    def take_output(
        self, predicate: Callable[[str], bool] | None = None
    ) -> str | None:
        return self.output_buffer.take_matching(predicate)


class Chest(Entity):
    """1x1 storage container holding up to CHEST_ITEM_LIMIT items."""

    def __init__(self, x: int, y: int) -> None:
        super().__init__(EntityType.CHEST, x, y)
        self.inventory: ItemBuffer = ItemBuffer(max_total=CHEST_ITEM_LIMIT)

    def total_items(self) -> int:
        return self.inventory.total()

    def can_add_item(self, item_type: str = "", count: int = 1) -> bool:
        amount = require_strict_int(count, "count")
        if amount <= 0:
            return False
        return self.inventory.can_add(item_type, amount)

    def add_item(self, item_type: str, count: int = 1) -> bool:
        return self.inventory.add(item_type, count)

    def take_item(
        self, predicate: Callable[[str], bool] | None = None
    ) -> str | None:
        return self.inventory.take_matching(predicate)


class Pole(Entity):
    """1x1 power pole connecting networks within wire_reach and powering supply_radius."""

    def __init__(self, x: int, y: int) -> None:
        super().__init__(EntityType.POLE, x, y)
        self.wire_reach: int = POLE_WIRE_REACH
        self.supply_radius: int = POLE_SUPPLY_RADIUS

    def in_wire_reach(self, other: "Pole") -> bool:
        dx = self.x - other.x
        dy = self.y - other.y
        return dx * dx + dy * dy <= self.wire_reach * self.wire_reach

    def covers_point(self, cx: int, cy: int) -> bool:
        return (
            abs(cx - self.x) <= self.supply_radius
            and abs(cy - self.y) <= self.supply_radius
        )

    def covers_entity(self, entity: Entity) -> bool:
        return any(self.covers_point(cx, cy) for cx, cy in entity.occupied_cells())


class Generator(Entity):
    """2x2 constant-output power generator."""

    def __init__(self, x: int, y: int) -> None:
        super().__init__(EntityType.GENERATOR, x, y)


def create_entity(
    entity_type: str | EntityType,
    x: int,
    y: int,
    direction: str | Direction | None = None,
    *,
    tier: int = 1,
) -> Entity:
    """Factory function constructing a validated entity instance."""
    et = parse_entity_type(entity_type)
    validated_tier = validate_tier(tier)
    if validated_tier != 1 and not supports_tier(et):
        raise ValueError(f"Entity type {et.value!r} does not support speed tiers")

    parsed_dir = parse_direction(direction)
    if needs_direction(et):
        if parsed_dir is None:
            raise ValueError(f"Entity type {et.value!r} requires a direction")
    elif parsed_dir is not None:
        raise ValueError(f"Entity type {et.value!r} does not take a direction")

    if et is EntityType.BELT:
        assert parsed_dir is not None
        return Belt(x, y, parsed_dir, tier=validated_tier)
    if et is EntityType.INSERTER:
        assert parsed_dir is not None
        return Inserter(x, y, parsed_dir, tier=validated_tier)
    if et is EntityType.MINER:
        return Miner(x, y)
    if et is EntityType.ASSEMBLER:
        return Assembler(x, y)
    if et is EntityType.CHEST:
        return Chest(x, y)
    if et is EntityType.POLE:
        return Pole(x, y)
    if et is EntityType.GENERATOR:
        return Generator(x, y)

    raise ValueError(f"Entity type {et.value!r} is not yet implemented")