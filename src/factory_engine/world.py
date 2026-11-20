from factory_engine.constants import (
    ENTITY_SIZES,
    Direction,
    get_entity_size,
    is_valid_entity_type,
)
from factory_engine.entities import (
    Belt,
    Deposit,
    Entity,
    create_entity,
)


POWER_RELATED_TYPES: set[str] = {
    "pole",
    "generator",
    "miner",
    "assembler",
    "inserter",
}


class World:
    def __init__(self, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            raise ValueError(f"World dimensions must be positive: {width}x{height}")

        self.width: int = width
        self.height: int = height
        self.cells: list[Entity | None] = [None] * (width * height)
        self.entities: dict[tuple[int, int], Entity] = {}
        self.by_type: dict[str, list[Entity]] = {
            entity_type: [] for entity_type in ENTITY_SIZES
        }
        self.resources: dict[tuple[int, int], Deposit] = {}
        self.tick: int = 0
        self.next_item_uid: int = 1

        self.belts_dirty: bool = True
        self.power_dirty: bool = True

    def allocate_item_uid(self) -> int:
        uid = self.next_item_uid
        self.next_item_uid += 1
        return uid

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def rect_in_bounds(self, x: int, y: int, width: int, height: int) -> bool:
        if width <= 0 or height <= 0:
            return False
        return (
            x >= 0
            and y >= 0
            and x + width <= self.width
            and y + height <= self.height
        )

    def _cell_index(self, x: int, y: int) -> int:
        return y * self.width + x

    def get_entity_at(self, x: int, y: int) -> Entity | None:
        if not self.in_bounds(x, y):
            return None
        return self.cells[self._cell_index(x, y)]

    def get_entity_by_origin(self, x: int, y: int) -> Entity | None:
        return self.entities.get((x, y))

    def get_belt_at(self, x: int, y: int) -> Belt | None:
        entity = self.get_entity_at(x, y)
        if isinstance(entity, Belt):
            return entity
        return None

    def is_cell_occupied(self, x: int, y: int) -> bool:
        return self.get_entity_at(x, y) is not None

    def can_place_rect(self, x: int, y: int, width: int, height: int) -> bool:
        if not self.rect_in_bounds(x, y, width, height):
            return False
        for dy in range(height):
            row_offset = (y + dy) * self.width
            for dx in range(width):
                if self.cells[row_offset + x + dx] is not None:
                    return False
        return True

    def can_place_entity(self, entity_type: str, x: int, y: int) -> bool:
        if not is_valid_entity_type(entity_type):
            return False
        width, height = get_entity_size(entity_type)
        return self.can_place_rect(x, y, width, height)

    def _insert_sorted_by_type(self, entity: Entity) -> None:
        target_list = self.by_type[entity.entity_type]
        new_key = entity.sort_key()
        insert_idx = len(target_list)
        for i, existing in enumerate(target_list):
            if new_key < existing.sort_key():
                insert_idx = i
                break
        target_list.insert(insert_idx, entity)

    def _mark_dirty_for_entity(self, entity_type: str) -> None:
        if entity_type == "belt":
            self.belts_dirty = True
        if entity_type in POWER_RELATED_TYPES:
            self.power_dirty = True

    def add_entity(self, entity: Entity) -> bool:
        if not self.can_place_rect(entity.x, entity.y, entity.width, entity.height):
            return False

        for cx, cy in entity.occupied_cells():
            self.cells[self._cell_index(cx, cy)] = entity

        self.entities[(entity.x, entity.y)] = entity
        self._insert_sorted_by_type(entity)
        self._mark_dirty_for_entity(entity.entity_type)
        return True

    def place_entity(
        self,
        entity_type: str,
        x: int,
        y: int,
        direction: str | Direction | None = None,
    ) -> Entity | None:
        entity = create_entity(entity_type, x, y, direction)
        if not self.add_entity(entity):
            return None
        return entity

    def remove_entity_at(self, x: int, y: int) -> Entity | None:
        entity = self.get_entity_at(x, y)
        if entity is None:
            return None

        for cx, cy in entity.occupied_cells():
            self.cells[self._cell_index(cx, cy)] = None

        del self.entities[(entity.x, entity.y)]
        self.by_type[entity.entity_type].remove(entity)
        entity.network_id = None
        self._mark_dirty_for_entity(entity.entity_type)
        return entity

    def add_resource_patch(
        self,
        x: int,
        y: int,
        w: int,
        h: int,
        resource: str,
        amount: int,
    ) -> None:
        if not resource:
            raise ValueError("Resource name cannot be empty")
        if amount <= 0:
            raise ValueError(f"Resource amount must be positive: {amount}")
        if not self.rect_in_bounds(x, y, w, h):
            raise ValueError(
                f"Resource patch ({x}, {y}, {w}, {h}) is out of world bounds "
                f"{self.width}x{self.height}"
            )

        for dy in range(h):
            for dx in range(w):
                cx = x + dx
                cy = y + dy
                existing = self.resources.get((cx, cy))
                if existing is not None and existing.resource_type == resource:
                    existing.amount += amount
                else:
                    self.resources[(cx, cy)] = Deposit(
                        x=cx,
                        y=cy,
                        resource_type=resource,
                        amount=amount,
                    )

    def get_resource_at(self, x: int, y: int) -> Deposit | None:
        if not self.in_bounds(x, y):
            return None
        deposit = self.resources.get((x, y))
        if deposit is not None and deposit.is_depleted():
            return None
        return deposit

    def cleanup_depleted_resource(self, x: int, y: int) -> None:
        deposit = self.resources.get((x, y))
        if deposit is not None and deposit.is_depleted():
            del self.resources[(x, y)]

    def get_deposits_under_entity(self, entity: Entity) -> list[Deposit]:
        found: list[Deposit] = []
        for cx, cy in sorted(entity.occupied_cells()):
            deposit = self.get_resource_at(cx, cy)
            if deposit is not None and not deposit.is_depleted():
                found.append(deposit)
        return found

    def get_entities_of_type(self, entity_type: str) -> list[Entity]:
        if entity_type not in self.by_type:
            raise ValueError(f"Unknown entity type: {entity_type!r}")
        return list(self.by_type[entity_type])

    def get_all_entities(self) -> list[Entity]:
        result = list(self.entities.values())
        result.sort(key=lambda e: e.sort_key())
        return result

    def query_entities(
        self,
        entity_type: str | None = None,
        rect: tuple[int, int, int, int] | None = None,
    ) -> list[Entity]:
        if entity_type is not None:
            candidates = self.get_entities_of_type(entity_type)
        else:
            candidates = self.get_all_entities()

        if rect is None:
            return candidates

        rx, ry, rw, rh = rect
        if rw <= 0 or rh <= 0:
            return []

        rx_end = rx + rw
        ry_end = ry + rh
        filtered: list[Entity] = []
        for entity in candidates:
            if (
                entity.x < rx_end
                and entity.x + entity.width > rx
                and entity.y < ry_end
                and entity.y + entity.height > ry
            ):
                filtered.append(entity)
        return filtered

    def get_neighbors(self, x: int, y: int) -> dict[Direction, Entity | None]:
        result: dict[Direction, Entity | None] = {}
        for direction in Direction:
            dx, dy = direction.delta()
            result[direction] = self.get_entity_at(x + dx, y + dy)
        return result

    def entity_count(self) -> int:
        return len(self.entities)

    def clear(self) -> None:
        self.cells = [None] * (self.width * self.height)
        self.entities.clear()
        for entity_list in self.by_type.values():
            entity_list.clear()
        self.resources.clear()
        self.tick = 0
        self.next_item_uid = 1
        self.belts_dirty = True
        self.power_dirty = True

    def validate_spatial_index(self) -> None:
        expected_cells: dict[tuple[int, int], Entity] = {}
        for origin, entity in self.entities.items():
            if origin != (entity.x, entity.y):
                raise AssertionError(f"Entity origin mismatch: {origin}")
            for cx, cy in entity.occupied_cells():
                if not self.in_bounds(cx, cy):
                    raise AssertionError(f"Entity cell out of bounds: {(cx, cy)}")
                if (cx, cy) in expected_cells:
                    raise AssertionError(f"Overlapping entities at {(cx, cy)}")
                expected_cells[(cx, cy)] = entity

        for y in range(self.height):
            for x in range(self.width):
                actual = self.cells[self._cell_index(x, y)]
                expected = expected_cells.get((x, y))
                if actual is not expected:
                    raise AssertionError(f"Grid mismatch at {(x, y)}")

        total_in_by_type = 0
        for entity_type, entity_list in self.by_type.items():
            total_in_by_type += len(entity_list)
            for i, entity in enumerate(entity_list):
                if entity.entity_type != entity_type:
                    raise AssertionError("Wrong entity type bucket")
                if i > 0 and entity_list[i - 1].sort_key() >= entity.sort_key():
                    raise AssertionError(f"by_type[{entity_type}] is not sorted")

        if total_in_by_type != len(self.entities):
            raise AssertionError("by_type count does not match entities dict")