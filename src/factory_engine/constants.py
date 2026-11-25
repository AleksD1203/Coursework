from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum, StrEnum
from types import MappingProxyType


# Core simulation constants
TICKS_PER_SECOND = 60
TILE_LEN = 256
ITEM_SPACING = 64
MAX_POS = TILE_LEN - 1
DROP_POS = 128

# Tiered belt / underground / splitter speeds (units per tick; all <= ITEM_SPACING)
VALID_TIERS: tuple[int, ...] = (1, 2, 3)
BELT_SPEED_BY_TIER: Mapping[int, int] = MappingProxyType({1: 8, 2: 16, 3: 24})
UNDERGROUND_MAX_SPAN = 4

# Power network settings (satisfaction is in thousandths: 1000 == 100%)
SATISFACTION_ONE = 1000
POLE_WIRE_REACH = 7
POLE_SUPPLY_RADIUS = 2

# Entity power (kW)
INSERTER_POWER_KW_BY_TIER: Mapping[int, int] = MappingProxyType({1: 13, 2: 26, 3: 40})
MINER_POWER_KW = 90
ASSEMBLER_POWER_KW = 75
GENERATOR_POWER_KW = 900

# Timings (in full-power ticks) and buffer limits (in items)
MINER_TIME_TICKS = 60
INSERTER_SWING_TICKS_BY_TIER: Mapping[int, int] = MappingProxyType({1: 15, 2: 8, 3: 5})
MINER_OUTPUT_TOTAL_LIMIT = 10
ASSEMBLER_INPUT_LIMIT_PER_ITEM = 10
ASSEMBLER_OUTPUT_TOTAL_LIMIT = 10
CHEST_ITEM_LIMIT = 100

# Validation and persistence limits
MAX_ITEM_NAME_LEN = 64
MAX_WORLD_DIM = 1024
MAX_WORLD_CELLS = 262_144
MAX_SAVE_ENTITIES = 100_000
MAX_SAVE_BYTES = 50_000_000
SAVE_FORMAT_VERSION = 2


def require_strict_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Expected integer for {name}, got {type(value).__name__}")
    return value


def validate_item_name(item_type: object) -> str:
    if not isinstance(item_type, str):
        raise ValueError(f"Item type must be a string, got {type(item_type).__name__}")
    if not item_type or len(item_type) > MAX_ITEM_NAME_LEN or item_type != item_type.strip():
        raise ValueError(
            f"Item type must be 1..{MAX_ITEM_NAME_LEN} chars with no outer whitespace: {item_type!r}"
        )
    return item_type


def ticks_to_progress(ticks: int) -> int:
    return ticks * SATISFACTION_ONE


class Direction(StrEnum):
    """Cardinal directions on the 2D grid (x right, y down)."""

    N = "N"
    E = "E"
    S = "S"
    W = "W"

    def delta(self) -> tuple[int, int]:
        """Return the (dx, dy) unit step in this direction."""
        return _DIRECTION_DELTAS[self]

    def opposite(self) -> "Direction":
        """Return the direction rotated by 180 degrees."""
        return _CLOCKWISE_DIRECTIONS[(_DIRECTION_INDEX[self] + 2) % 4]

    def turn_right(self) -> "Direction":
        """Return the direction rotated 90 degrees clockwise."""
        return _CLOCKWISE_DIRECTIONS[(_DIRECTION_INDEX[self] + 1) % 4]

    def turn_left(self) -> "Direction":
        """Return the direction rotated 90 degrees counter-clockwise."""
        return _CLOCKWISE_DIRECTIONS[(_DIRECTION_INDEX[self] - 1) % 4]


_CLOCKWISE_DIRECTIONS: tuple[Direction, ...] = (
    Direction.N,
    Direction.E,
    Direction.S,
    Direction.W,
)
_DIRECTION_INDEX: Mapping[Direction, int] = MappingProxyType(
    {d: idx for idx, d in enumerate(_CLOCKWISE_DIRECTIONS)}
)
_DIRECTION_DELTAS: Mapping[Direction, tuple[int, int]] = MappingProxyType(
    {
        Direction.N: (0, -1),
        Direction.E: (1, 0),
        Direction.S: (0, 1),
        Direction.W: (-1, 0),
    }
)
_DIRECTION_BY_STR: Mapping[str, Direction] = MappingProxyType(
    {d.value: d for d in _CLOCKWISE_DIRECTIONS}
)


def parse_direction(value: str | Direction | None) -> Direction | None:
    if value is None:
        return None
    if isinstance(value, Direction):
        return value
    if isinstance(value, str):
        parsed = _DIRECTION_BY_STR.get(value.upper())
        if parsed is not None:
            return parsed
    raise ValueError(f"Unknown direction: {value!r}")


class LaneSide(Enum):
    """Side of a two-lane belt relative to its direction of travel (LEFT is to the left)."""
    LEFT = 0
    RIGHT = 1

    @property
    def index(self) -> int:
        return self.value

    @property
    def json_key(self) -> str:
        return "left" if self is LaneSide.LEFT else "right"

    def opposite(self) -> "LaneSide":
        return LaneSide.RIGHT if self is LaneSide.LEFT else LaneSide.LEFT


def parse_lane_side(value: object) -> LaneSide:
    if isinstance(value, LaneSide):
        return value
    raise ValueError(f"Expected LaneSide, got {value!r}")


def parse_lane_side_name(value: object) -> LaneSide:
    if value == "left":
        return LaneSide.LEFT
    if value == "right":
        return LaneSide.RIGHT
    raise ValueError(f"Expected lane side 'left' or 'right', got {value!r}")


class UndergroundMode(StrEnum):
    """Role of an underground belt tile."""
    ENTRANCE = "entrance"
    EXIT = "exit"


_UNDERGROUND_MODE_BY_STR: Mapping[str, UndergroundMode] = MappingProxyType(
    {m.value: m for m in UndergroundMode}
)


def parse_underground_mode(value: object) -> UndergroundMode:
    if isinstance(value, UndergroundMode):
        return value
    if isinstance(value, str):
        parsed = _UNDERGROUND_MODE_BY_STR.get(value.lower())
        if parsed is not None:
            return parsed
    raise ValueError(f"Unknown underground mode: {value!r}")


class EntityType(StrEnum):
    BELT = "belt"
    UNDERGROUND_BELT = "underground_belt"
    SPLITTER = "splitter"
    INSERTER = "inserter"
    MINER = "miner"
    ASSEMBLER = "assembler"
    CHEST = "chest"
    POLE = "pole"
    GENERATOR = "generator"


_ENTITY_TYPE_BY_STR: Mapping[str, EntityType] = MappingProxyType(
    {et.value: et for et in EntityType}
)


def parse_entity_type(value: object) -> EntityType:
    if isinstance(value, EntityType):
        return value
    if isinstance(value, str):
        parsed = _ENTITY_TYPE_BY_STR.get(value)
        if parsed is not None:
            return parsed
    raise ValueError(f"Unknown entity type: {value!r}")


class InserterState(StrEnum):
    IDLE = "IDLE"
    SWING_TO_DROP = "SWING_TO_DROP"
    WAIT_DROP = "WAIT_DROP"
    SWING_BACK = "SWING_BACK"


@dataclass(frozen=True, slots=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        require_strict_int(self.x, "rect.x")
        require_strict_int(self.y, "rect.y")
        w = require_strict_int(self.width, "rect.width")
        h = require_strict_int(self.height, "rect.height")
        if w < 0 or h < 0:
            raise ValueError(f"Rect width and height must be non-negative, got ({w}, {h})")

    def overlaps_box(self, x: int, y: int, width: int, height: int) -> bool:
        if self.width == 0 or self.height == 0:
            return False
        return not (
            x + width <= self.x
            or x >= self.x + self.width
            or y + height <= self.y
            or y >= self.y + self.height
        )


def parse_rect(value: Rect | tuple[int, int, int, int]) -> Rect:
    if isinstance(value, Rect):
        return value
    if isinstance(value, tuple) and len(value) == 4:
        return Rect(value[0], value[1], value[2], value[3])
    raise ValueError(f"Expected Rect or 4-tuple (x, y, width, height), got {value!r}")


BASE_ENTITY_SIZES: Mapping[EntityType, tuple[int, int]] = MappingProxyType(
    {
        EntityType.BELT: (1, 1),
        EntityType.UNDERGROUND_BELT: (1, 1),
        EntityType.INSERTER: (1, 1),
        EntityType.MINER: (2, 2),
        EntityType.ASSEMBLER: (3, 3),
        EntityType.CHEST: (1, 1),
        EntityType.POLE: (1, 1),
        EntityType.GENERATOR: (2, 2),
    }
)

DIRECTIONAL_ENTITY_TYPES: frozenset[EntityType] = frozenset(
    {
        EntityType.BELT,
        EntityType.UNDERGROUND_BELT,
        EntityType.SPLITTER,
        EntityType.INSERTER,
    }
)

TIERED_ENTITY_TYPES: frozenset[EntityType] = frozenset(
    {
        EntityType.BELT,
        EntityType.UNDERGROUND_BELT,
        EntityType.SPLITTER,
        EntityType.INSERTER,
    }
)

FIXED_POWER_CONSUMPTION_KW: Mapping[EntityType, int] = MappingProxyType(
    {
        EntityType.MINER: MINER_POWER_KW,
        EntityType.ASSEMBLER: ASSEMBLER_POWER_KW,
    }
)

POWER_PRODUCTION_KW: Mapping[EntityType, int] = MappingProxyType(
    {
        EntityType.GENERATOR: GENERATOR_POWER_KW,
    }
)

POWER_CONSUMER_TYPES: frozenset[EntityType] = frozenset(
    FIXED_POWER_CONSUMPTION_KW.keys()
) | frozenset({EntityType.INSERTER})
POWER_PRODUCER_TYPES: frozenset[EntityType] = frozenset(POWER_PRODUCTION_KW.keys())
POWER_RELATED_TYPES: frozenset[EntityType] = (
    POWER_CONSUMER_TYPES | POWER_PRODUCER_TYPES | frozenset({EntityType.POLE})
)


def validate_tier(tier: object) -> int:
    val = require_strict_int(tier, "tier")
    if val not in VALID_TIERS:
        raise ValueError(f"Tier must be one of {VALID_TIERS}, got {val}")
    return val


def get_belt_speed(tier: int) -> int:
    """Return belt speed (units/tick) for a validated tier."""
    return BELT_SPEED_BY_TIER[validate_tier(tier)]


def get_inserter_swing_ticks(tier: int) -> int:
    """Return inserter swing duration (full-power ticks) for a validated tier."""
    return INSERTER_SWING_TICKS_BY_TIER[validate_tier(tier)]


def needs_direction(entity_type: str | EntityType) -> bool:
    """Return True if the given entity type requires a cardinal direction."""
    return parse_entity_type(entity_type) in DIRECTIONAL_ENTITY_TYPES


def supports_tier(entity_type: str | EntityType) -> bool:
    """Return True if the given entity type supports speed tiers 1..3."""
    return parse_entity_type(entity_type) in TIERED_ENTITY_TYPES


def get_entity_size(
    entity_type: str | EntityType, direction: Direction | None = None
) -> tuple[int, int]:
    """Return (width, height) in grid cells for the given entity type and direction."""
    et = parse_entity_type(entity_type)
    if et is EntityType.SPLITTER:
        if direction is None:
            raise ValueError("Splitter requires a direction to determine its footprint")
        return (2, 1) if direction in (Direction.N, Direction.S) else (1, 2)
    return BASE_ENTITY_SIZES[et]


def get_power_consumption_kw(entity_type: str | EntityType, tier: int = 1) -> int:
    """Return power consumption in kW for the given entity type and tier."""
    et = parse_entity_type(entity_type)
    if et is EntityType.INSERTER:
        return INSERTER_POWER_KW_BY_TIER[validate_tier(tier)]
    return FIXED_POWER_CONSUMPTION_KW.get(et, 0)


def get_power_production_kw(entity_type: str | EntityType) -> int:
    """Return power production in kW for the given entity type."""
    return POWER_PRODUCTION_KW.get(parse_entity_type(entity_type), 0)


@dataclass(frozen=True, slots=True)
class Recipe:
    recipe_id: str
    ingredients: Mapping[str, int]
    results: Mapping[str, int]
    time_ticks: int

    def total_progress(self) -> int:
        """Return progress required to complete one crafting cycle."""
        return ticks_to_progress(self.time_ticks)


RECIPES: Mapping[str, Recipe] = MappingProxyType(
    {
        "iron-plate": Recipe(
            recipe_id="iron-plate",
            ingredients=MappingProxyType({"iron-ore": 1}),
            results=MappingProxyType({"iron-plate": 1}),
            time_ticks=192,
        ),
        "gear": Recipe(
            recipe_id="gear",
            ingredients=MappingProxyType({"iron-plate": 2}),
            results=MappingProxyType({"gear": 1}),
            time_ticks=30,
        ),
    }
)


def get_recipe(recipe_id: object) -> Recipe:
    if not isinstance(recipe_id, str) or recipe_id not in RECIPES:
        raise ValueError(f"Unknown recipe: {recipe_id!r}")
    return RECIPES[recipe_id]