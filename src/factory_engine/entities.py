from dataclasses import dataclass, field

from factory_engine.constants import (
    ASSEMBLER_INPUT_LIMIT,
    ASSEMBLER_OUTPUT_LIMIT,
    CHEST_SLOT_LIMIT,
    DROP_POS,
    ITEM_SPACING,
    LEFT_LANE,
    MAX_POS,
    MINER_BUFFER_LIMIT,
    POLE_AREA,
    POLE_REACH,
    RIGHT_LANE,
    Direction,
    InserterState,
    Recipe,
    get_entity_size,
    get_power_consumption,
    get_power_production,
    get_recipe,
    is_valid_entity_type,
    needs_direction,
    parse_direction,
)


@dataclass
class Deposit:
    x: int
    y: int
    resource_type: str
    amount: int

    def __post_init__(self) -> None:
        if not self.resource_type:
            raise ValueError("Resource type cannot be empty")
        if self.amount < 0:
            raise ValueError(f"Deposit amount cannot be negative: {self.amount}")

    def is_depleted(self) -> bool:
        return self.amount <= 0

    def extract(self, count: int = 1) -> int:
        if count <= 0:
            raise ValueError(f"Extract count must be positive: {count}")
        taken = min(self.amount, count)
        self.amount -= taken
        return taken


@dataclass
class BeltItem:
    uid: int
    item_type: str
    pos: int

    def __post_init__(self) -> None:
        if self.uid < 0:
            raise ValueError(f"Item uid cannot be negative: {self.uid}")
        if not self.item_type:
            raise ValueError("Item type cannot be empty")
        if self.pos < 0 or self.pos > MAX_POS:
            raise ValueError(f"Item pos out of range 0..{MAX_POS}: {self.pos}")


class Lane:
    def __init__(self, items: list[BeltItem] | None = None) -> None:
        self.items: list[BeltItem] = []
        if items is not None:
            for item in items:
                self.items.append(item)
            self.items.sort(key=lambda it: it.pos, reverse=True)
            self.validate_invariants()

    def is_empty(self) -> bool:
        return len(self.items) == 0

    def count(self) -> int:
        return len(self.items)

    def front_item(self) -> BeltItem | None:
        if not self.items:
            return None
        return self.items[0]

    def back_item(self) -> BeltItem | None:
        if not self.items:
            return None
        return self.items[-1]

    def can_insert_at(self, pos: int) -> bool:
        if pos < 0 or pos > MAX_POS:
            return False
        for existing in self.items:
            if abs(existing.pos - pos) < ITEM_SPACING:
                return False
        return True

    def insert_item(self, item: BeltItem) -> bool:
        if not self.can_insert_at(item.pos):
            return False
        self.items.append(item)
        self.items.sort(key=lambda it: it.pos, reverse=True)
        return True

    def take_front_item(self) -> BeltItem | None:
        if not self.items:
            return None
        return self.items.pop(0)

    def validate_invariants(self) -> None:
        for i, item in enumerate(self.items):
            if item.pos < 0 or item.pos > MAX_POS:
                raise ValueError(f"Lane item pos out of bounds: {item.pos}")
            if i > 0:
                prev_item = self.items[i - 1]
                if prev_item.pos < item.pos:
                    raise ValueError("Lane items are not sorted descending by pos")
                if prev_item.pos - item.pos < ITEM_SPACING:
                    raise ValueError(
                        f"Lane spacing violated: {prev_item.pos} and {item.pos}"
                    )


class Entity:
    def __init__(
        self,
        entity_type: str,
        x: int,
        y: int,
        direction: Direction | None = None,
    ) -> None:
        width, height = get_entity_size(entity_type)
        self.entity_type: str = entity_type
        self.x: int = x
        self.y: int = y
        self.width: int = width
        self.height: int = height
        self.direction: Direction | None = direction
        self.network_id: int | None = None

    def occupied_cells(self) -> list[tuple[int, int]]:
        cells: list[tuple[int, int]] = []
        for dy in range(self.height):
            for dx in range(self.width):
                cells.append((self.x + dx, self.y + dy))
        return cells

    def covers_cell(self, cx: int, cy: int) -> bool:
        return (
            self.x <= cx < self.x + self.width
            and self.y <= cy < self.y + self.height
        )

    def power_consumption(self) -> int:
        return get_power_consumption(self.entity_type)

    def power_production(self) -> int:
        return get_power_production(self.entity_type)

    def sort_key(self) -> tuple[int, int]:
        return (self.x, self.y)


class Belt(Entity):
    def __init__(self, x: int, y: int, direction: Direction) -> None:
        super().__init__("belt", x, y, direction)
        self.lanes: tuple[Lane, Lane] = (Lane(), Lane())
        self.next_belt: Belt | None = None
        self.side_load_target_lane: int | None = None

    def get_lane(self, lane_index: int) -> Lane:
        if lane_index not in (LEFT_LANE, RIGHT_LANE):
            raise ValueError(f"Invalid lane index: {lane_index}")
        return self.lanes[lane_index]

    def next_cell(self) -> tuple[int, int]:
        assert self.direction is not None
        dx, dy = self.direction.delta()
        return (self.x + dx, self.y + dy)

    def item_count(self) -> int:
        return self.lanes[LEFT_LANE].count() + self.lanes[RIGHT_LANE].count()

    def can_drop_item(
        self, lane_index: int = RIGHT_LANE, pos: int = DROP_POS
    ) -> bool:
        return self.get_lane(lane_index).can_insert_at(pos)

    def try_drop_item(
        self,
        uid: int,
        item_type: str,
        lane_index: int = RIGHT_LANE,
        pos: int = DROP_POS,
    ) -> bool:
        lane = self.get_lane(lane_index)
        if not lane.can_insert_at(pos):
            return False
        return lane.insert_item(BeltItem(uid=uid, item_type=item_type, pos=pos))

    def try_take_item(self) -> BeltItem | None:
        item = self.lanes[LEFT_LANE].take_front_item()
        if item is not None:
            return item
        return self.lanes[RIGHT_LANE].take_front_item()


class Inserter(Entity):
    def __init__(self, x: int, y: int, direction: Direction) -> None:
        super().__init__("inserter", x, y, direction)
        self.state: InserterState = InserterState.IDLE
        self.held_item: str | None = None
        self.held_uid: int | None = None
        self.progress: int = 0

    def pickup_cell(self) -> tuple[int, int]:
        assert self.direction is not None
        dx, dy = self.direction.opposite().delta()
        return (self.x + dx, self.y + dy)

    def drop_cell(self) -> tuple[int, int]:
        assert self.direction is not None
        dx, dy = self.direction.delta()
        return (self.x + dx, self.y + dy)

    def is_holding_item(self) -> bool:
        return self.held_item is not None


class Miner(Entity):
    def __init__(self, x: int, y: int) -> None:
        super().__init__("miner", x, y, direction=None)
        self.output_buffer: dict[str, int] = {}
        self.progress: int = 0

    def total_buffered(self) -> int:
        return sum(self.output_buffer.values())

    def can_produce(self) -> bool:
        return self.total_buffered() < MINER_BUFFER_LIMIT

    def add_output(self, item_type: str, count: int = 1) -> None:
        if count <= 0:
            raise ValueError(f"Count must be positive: {count}")
        self.output_buffer[item_type] = self.output_buffer.get(item_type, 0) + count

    def has_output(self) -> bool:
        return self.total_buffered() > 0

    def take_output(self) -> str | None:
        for item_type in sorted(self.output_buffer.keys()):
            if self.output_buffer[item_type] > 0:
                self.output_buffer[item_type] -= 1
                if self.output_buffer[item_type] == 0:
                    del self.output_buffer[item_type]
                return item_type
        return None


class Assembler(Entity):
    def __init__(self, x: int, y: int) -> None:
        super().__init__("assembler", x, y, direction=None)
        self.recipe_id: str | None = None
        self.input_buffer: dict[str, int] = {}
        self.output_buffer: dict[str, int] = {}
        self.progress: int = 0

    def set_recipe(self, recipe_id: str | None) -> None:
        if recipe_id is None:
            self.recipe_id = None
            self.progress = 0
            return
        # Validate recipe exists
        get_recipe(recipe_id)
        if self.recipe_id != recipe_id:
            self.recipe_id = recipe_id
            self.progress = 0

    def get_current_recipe(self) -> Recipe | None:
        if self.recipe_id is None:
            return None
        return get_recipe(self.recipe_id)

    def can_accept_input(self, item_type: str) -> bool:
        recipe = self.get_current_recipe()
        if recipe is None:
            return False
        if item_type not in recipe.ingredients:
            return False
        current = self.input_buffer.get(item_type, 0)
        return current < ASSEMBLER_INPUT_LIMIT

    def add_input(self, item_type: str, count: int = 1) -> bool:
        if count <= 0:
            raise ValueError(f"Count must be positive: {count}")
        if not self.can_accept_input(item_type):
            return False
        self.input_buffer[item_type] = self.input_buffer.get(item_type, 0) + count
        return True

    def has_ingredients(self) -> bool:
        recipe = self.get_current_recipe()
        if recipe is None:
            return False
        for item_type, needed in recipe.ingredients.items():
            if self.input_buffer.get(item_type, 0) < needed:
                return False
        return True

    def total_output(self) -> int:
        return sum(self.output_buffer.values())

    def can_store_output(self) -> bool:
        recipe = self.get_current_recipe()
        if recipe is None:
            return False
        produced_count = sum(recipe.results.values())
        return self.total_output() + produced_count <= ASSEMBLER_OUTPUT_LIMIT

    def can_craft(self) -> bool:
        return self.has_ingredients() and self.can_store_output()

    def finish_craft_cycle(self) -> bool:
        recipe = self.get_current_recipe()
        if recipe is None or not self.can_craft():
            return False
        for item_type, needed in recipe.ingredients.items():
            self.input_buffer[item_type] -= needed
            if self.input_buffer[item_type] == 0:
                del self.input_buffer[item_type]
        for item_type, produced in recipe.results.items():
            self.output_buffer[item_type] = (
                self.output_buffer.get(item_type, 0) + produced
            )
        return True

    def has_output(self) -> bool:
        return self.total_output() > 0

    def take_output(self) -> str | None:
        for item_type in sorted(self.output_buffer.keys()):
            if self.output_buffer[item_type] > 0:
                self.output_buffer[item_type] -= 1
                if self.output_buffer[item_type] == 0:
                    del self.output_buffer[item_type]
                return item_type
        return None


class Chest(Entity):
    def __init__(self, x: int, y: int) -> None:
        super().__init__("chest", x, y, direction=None)
        self.inventory: dict[str, int] = {}

    def total_items(self) -> int:
        return sum(self.inventory.values())

    def can_add_item(self, count: int = 1) -> bool:
        if count <= 0:
            return False
        return self.total_items() + count <= CHEST_SLOT_LIMIT

    def add_item(self, item_type: str, count: int = 1) -> bool:
        if count <= 0:
            raise ValueError(f"Count must be positive: {count}")
        if not self.can_add_item(count):
            return False
        self.inventory[item_type] = self.inventory.get(item_type, 0) + count
        return True

    def has_items(self) -> bool:
        return self.total_items() > 0

    def take_item(self, item_type: str | None = None) -> str | None:
        if item_type is not None:
            if self.inventory.get(item_type, 0) <= 0:
                return None
            self.inventory[item_type] -= 1
            if self.inventory[item_type] == 0:
                del self.inventory[item_type]
            return item_type

        for key in sorted(self.inventory.keys()):
            if self.inventory[key] > 0:
                self.inventory[key] -= 1
                if self.inventory[key] == 0:
                    del self.inventory[key]
                return key
        return None


class Pole(Entity):
    def __init__(self, x: int, y: int) -> None:
        super().__init__("pole", x, y, direction=None)
        self.reach: int = POLE_REACH
        self.area: int = POLE_AREA

    def in_wire_reach(self, other: "Pole") -> bool:
        dx = self.x - other.x
        dy = self.y - other.y
        return dx * dx + dy * dy <= self.reach * self.reach

    def covers_point(self, cx: int, cy: int) -> bool:
        return abs(cx - self.x) <= self.area and abs(cy - self.y) <= self.area

    def covers_entity(self, entity: Entity) -> bool:
        for cx, cy in entity.occupied_cells():
            if self.covers_point(cx, cy):
                return True
        return False


class Generator(Entity):
    def __init__(self, x: int, y: int) -> None:
        super().__init__("generator", x, y, direction=None)


def create_entity(
    entity_type: str,
    x: int,
    y: int,
    direction: str | Direction | None = None,
) -> Entity:
    if not is_valid_entity_type(entity_type):
        raise ValueError(f"Unknown entity type: {entity_type!r}")

    parsed_dir = parse_direction(direction)
    if needs_direction(entity_type):
        if parsed_dir is None:
            raise ValueError(f"Entity type {entity_type!r} requires a direction")
    else:
        if parsed_dir is not None:
            raise ValueError(f"Entity type {entity_type!r} does not take a direction")

    if entity_type == "belt":
        assert parsed_dir is not None
        return Belt(x, y, parsed_dir)
    if entity_type == "inserter":
        assert parsed_dir is not None
        return Inserter(x, y, parsed_dir)
    if entity_type == "miner":
        return Miner(x, y)
    if entity_type == "assembler":
        return Assembler(x, y)
    if entity_type == "chest":
        return Chest(x, y)
    if entity_type == "pole":
        return Pole(x, y)
    if entity_type == "generator":
        return Generator(x, y)

    raise ValueError(f"Unhandled entity type: {entity_type!r}")