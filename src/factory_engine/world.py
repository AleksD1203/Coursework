import bisect
from typing import TypeVar

from factory_engine.constants import (
    Direction,
    EntityType,
    MAX_WORLD_CELLS,
    MAX_WORLD_DIM,
    Rect,
    parse_entity_type,
    parse_rect,
    require_strict_int,
    validate_item_name,
)
from factory_engine.entities import (
    Belt,
    Deposit,
    Entity,
    create_entity,
)

TEntity = TypeVar("TEntity", bound=Entity)


class World:
    def __init__(self, width: int, height: int) -> None:
        w = require_strict_int(width, "width")
        h = require_strict_int(height, "height")
        if w <= 0 or h <= 0:
            raise ValueError(f"World dimensions must be positive: {w}x{h}")
        if w > MAX_WORLD_DIM or h > MAX_WORLD_DIM:
            raise ValueError(
                f"World dimensions exceed maximum {MAX_WORLD_DIM}: {w}x{h}"
            )
        if w * h > MAX_WORLD_CELLS:
            raise ValueError(
                f"World cell count {w * h} exceeds maximum {MAX_WORLD_CELLS}"
            )

        self.width: int = w
        self.height: int = h
        self.cells: list[Entity | None] = [None] * (w * h)
        self.entities: dict[tuple[int, int], Entity] = {}
        self.by_type: dict[EntityType, list[Entity]] = {
            entity_type: [] for entity_type in EntityType
        }
        self.deposits: dict[tuple[int, int], Deposit] = {}
        self.tick: int = 0
        self.next_item_uid: int = 1
        self.topology_version: int = 0

    def allocate_item_uid(self) -> int:
        uid = self.next_item_uid
        self.next_item_uid += 1
        return uid

    def in_bounds(self, x: int, y: int) -> bool:
        """Return True if (x, y) lies inside the world grid."""
        return 0 <= x < self.width and 0 <= y < self.height

    def rect_in_bounds(self, x: int, y: int, width: int, height: int) -> bool:
        """Return True if a non-empty box (x, y, width, height) lies fully inside the grid."""
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
        """Return the entity occupying cell (x, y) in O(1), or None if empty/out of bounds."""
        cx = require_strict_int(x, "x")
        cy = require_strict_int(y, "y")
        if not self.in_bounds(cx, cy):
            return None
        return self.cells[self._cell_index(cx, cy)]

    def get_belt_at(self, x: int, y: int) -> Belt | None:
        """Return the Belt at cell (x, y), or None if absent or a different entity type."""
        entity = self.get_entity_at(x, y)
        if isinstance(entity, Belt):
            return entity
        return None

    def can_place_rect(self, x: int, y: int, width: int, height: int) -> bool:
        """Return True if every cell in (x, y, width, height) is in bounds and unoccupied."""
        if not self.rect_in_bounds(x, y, width, height):
            return False
        for dy in range(height):
            row_offset = (y + dy) * self.width
            for dx in range(width):
                if self.cells[row_offset + x + dx] is not None:
                    return False
        return True

    def _insert_sorted_by_type(self, entity: Entity) -> None:
        target_list = self.by_type[entity.entity_type]
        bisect.insort(target_list, entity, key=lambda item: item.sort_key())

    def add_entity(self, entity: Entity) -> bool:
        """Insert a constructed entity into the grid if its footprint is free."""
        if not self.can_place_rect(entity.x, entity.y, entity.width, entity.height):
            return False

        for cx, cy in entity.occupied_cells():
            self.cells[self._cell_index(cx, cy)] = entity

        self.entities[(entity.x, entity.y)] = entity
        self._insert_sorted_by_type(entity)
        self.topology_version += 1
        return True

    def place_entity(
        self,
        entity_type: str | EntityType,
        x: int,
        y: int,
        direction: str | Direction | None = None,
        *,
        tier: int = 1,
    ) -> Entity | None:
        """Construct and place an entity, returning it on success or None if blocked."""
        entity = create_entity(entity_type, x, y, direction, tier=tier)
        if not self.add_entity(entity):
            return None
        return entity

    def remove_entity_at(self, x: int, y: int) -> Entity | None:
        """Remove the entity occupying cell (x, y), returning it or None if absent."""
        entity = self.get_entity_at(x, y)
        if entity is None:
            return None

        for cx, cy in entity.occupied_cells():
            self.cells[self._cell_index(cx, cy)] = None

        del self.entities[(entity.x, entity.y)]
        self.by_type[entity.entity_type].remove(entity)
        entity.network_id = None
        self.topology_version += 1
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
        """Add or replace ore deposits across rectangle (x, y, w, h) atomically."""
        rx = require_strict_int(x, "x")
        ry = require_strict_int(y, "y")
        rw = require_strict_int(w, "w")
        rh = require_strict_int(h, "h")
        item_type = validate_item_name(resource)
        qty = require_strict_int(amount, "amount")
        if qty <= 0:
            raise ValueError(f"Resource amount must be positive: {qty}")
        if not self.rect_in_bounds(rx, ry, rw, rh):
            raise ValueError(
                f"Resource patch ({rx}, {ry}, {rw}, {rh}) is out of world bounds "
                f"{self.width}x{self.height}"
            )

        for dy in range(rh):
            for dx in range(rw):
                cx = rx + dx
                cy = ry + dy
                existing = self.deposits.get((cx, cy))
                if existing is not None and existing.item_type == item_type:
                    existing.amount += qty
                else:
                    self.deposits[(cx, cy)] = Deposit(
                        x=cx,
                        y=cy,
                        item_type=item_type,
                        amount=qty,
                    )

    def get_deposit_at(self, x: int, y: int) -> Deposit | None:
        """Return the active deposit at (x, y), or None if absent/depleted/out of bounds."""
        cx = require_strict_int(x, "x")
        cy = require_strict_int(y, "y")
        if not self.in_bounds(cx, cy):
            return None
        deposit = self.deposits.get((cx, cy))
        if deposit is not None and deposit.is_depleted():
            return None
        return deposit

    def cleanup_depleted_deposit(self, x: int, y: int) -> None:
        """Remove a depleted deposit entry from the map."""
        deposit = self.deposits.get((x, y))
        if deposit is not None and deposit.is_depleted():
            del self.deposits[(x, y)]

    def get_deposits_under_entity(self, entity: Entity) -> list[Deposit]:
        """Return active deposits underneath entity cells in deterministic (x, y) order."""
        found: list[Deposit] = []
        for cx, cy in sorted(entity.occupied_cells()):
            deposit = self.get_deposit_at(cx, cy)
            if deposit is not None:
                found.append(deposit)
        return found

    def get_entities_of_type(self, entity_type: str | EntityType) -> list[Entity]:
        """Return a copy of the sorted entity list for the given entity type."""
        et = parse_entity_type(entity_type)
        return list(self.by_type[et])

    def get_entities_of_class(
        self, entity_type: EntityType, entity_cls: type[TEntity]
    ) -> list[TEntity]:
        """Return a typed list of entities for entity_type, raising TypeError on mismatch."""
        typed: list[TEntity] = []
        for entity in self.by_type[entity_type]:
            if not isinstance(entity, entity_cls):
                raise TypeError(
                    f"Expected {entity_cls.__name__} in bucket {entity_type.value!r}, "
                    f"got {type(entity).__name__}"
                )
            typed.append(entity)
        return typed

    def get_all_entities(self) -> list[Entity]:
        """Return all entities in deterministic (x, y) order."""
        result = list(self.entities.values())
        result.sort(key=lambda entity: entity.sort_key())
        return result

    def iter_entities_in_box(
        self, min_x: int, min_y: int, max_x: int, max_y: int
    ) -> list[Entity]:
        """Return distinct entities occupying cells within the clipped inclusive box."""
        x0 = max(0, min_x)
        x1 = min(self.width - 1, max_x)
        y0 = max(0, min_y)
        y1 = min(self.height - 1, max_y)
        if x0 > x1 or y0 > y1:
            return []

        seen: set[int] = set()
        found: list[Entity] = []
        for y in range(y0, y1 + 1):
            row_offset = y * self.width
            for x in range(x0, x1 + 1):
                entity = self.cells[row_offset + x]
                if entity is not None and id(entity) not in seen:
                    seen.add(id(entity))
                    found.append(entity)
        return found

    def query_entities(
        self,
        entity_type: str | EntityType | None = None,
        rect: Rect | tuple[int, int, int, int] | None = None,
    ) -> list[Entity]:
        """Return sorted entities filtered by optional entity_type and overlapping rect."""
        if entity_type is not None:
            candidates = self.get_entities_of_type(entity_type)
        else:
            candidates = self.get_all_entities()

        if rect is None:
            return candidates

        parsed_rect = parse_rect(rect)
        return [
            entity
            for entity in candidates
            if parsed_rect.overlaps_box(
                entity.x, entity.y, entity.width, entity.height
            )
        ]

    def query_deposits(
        self,
        rect: Rect | tuple[int, int, int, int] | None = None,
    ) -> list[Deposit]:
        """Return active deposits sorted by (x, y), optionally filtered by rect overlap."""
        parsed_rect = parse_rect(rect) if rect is not None else None
        found: list[Deposit] = []
        for coord in sorted(self.deposits):
            deposit = self.deposits[coord]
            if deposit.is_depleted():
                continue
            if parsed_rect is None or parsed_rect.overlaps_box(
                deposit.x, deposit.y, 1, 1
            ):
                found.append(deposit)
        return found

    def validate_spatial_index(self) -> None:
        tick = require_strict_int(self.tick, "world.tick")
        if tick < 0:
            raise ValueError(f"World tick cannot be negative: {tick}")
        next_uid = require_strict_int(self.next_item_uid, "world.next_item_uid")
        if next_uid < 1:
            raise ValueError(f"World next_item_uid must be >= 1: {next_uid}")
        version = require_strict_int(self.topology_version, "world.topology_version")
        if version < 0:
            raise ValueError(f"World topology_version cannot be negative: {version}")

        expected_cells: dict[tuple[int, int], Entity] = {}
        for origin, entity in self.entities.items():
            if origin != (entity.x, entity.y):
                raise ValueError(f"Entity origin mismatch: {origin} != {(entity.x, entity.y)}")
            for cx, cy in entity.occupied_cells():
                if not self.in_bounds(cx, cy):
                    raise ValueError(f"Entity cell out of bounds: {(cx, cy)}")
                if (cx, cy) in expected_cells:
                    raise ValueError(f"Overlapping entities at {(cx, cy)}")
                expected_cells[(cx, cy)] = entity

        for y in range(self.height):
            for x in range(self.width):
                actual = self.cells[self._cell_index(x, y)]
                expected = expected_cells.get((x, y))
                if actual is not expected:
                    raise ValueError(f"Grid mismatch at {(x, y)}")

        total_in_by_type = 0
        for entity_type, entity_list in self.by_type.items():
            total_in_by_type += len(entity_list)
            for i, entity in enumerate(entity_list):
                if entity.entity_type is not entity_type:
                    raise ValueError(
                        f"Wrong entity type bucket: {entity.entity_type} in {entity_type}"
                    )
                if i > 0 and entity_list[i - 1].sort_key() >= entity.sort_key():
                    raise ValueError(f"by_type[{entity_type.value}] is not sorted")

        if total_in_by_type != len(self.entities):
            raise ValueError("by_type count does not match entities dict")

        for coord, deposit in self.deposits.items():
            if coord != (deposit.x, deposit.y):
                raise ValueError(f"Deposit coordinate mismatch: {coord}")
            if not self.in_bounds(deposit.x, deposit.y):
                raise ValueError(f"Deposit out of bounds: {coord}")
            validate_item_name(deposit.item_type)
            amount = require_strict_int(deposit.amount, "deposit.amount")
            if amount <= 0:
                raise ValueError(f"Deposit at {coord} must have positive amount: {amount}")