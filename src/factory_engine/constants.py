from dataclasses import dataclass
from enum import Enum


# Basic simulation settings
TICKS_PER_SECOND = 60
TILE_LEN = 256
ITEM_SPACING = 64
MAX_POS = TILE_LEN - 1
BELT_SPEED = 8
DROP_POS = 128

# Belt lanes
LEFT_LANE = 0
RIGHT_LANE = 1

# Power network settings
SAT_ONE = 1000
POLE_REACH = 7
POLE_AREA = 2

# Entity power (kW)
INSERTER_POWER = 13
MINER_POWER = 90
ASSEMBLER_POWER = 75
GENERATOR_POWER = 900

# Timings and buffer limits
MINER_TIME_TICKS = 60
INSERTER_SWING_TICKS = 15
MINER_BUFFER_LIMIT = 10
ASSEMBLER_INPUT_LIMIT = 10
ASSEMBLER_OUTPUT_LIMIT = 10
CHEST_SLOT_LIMIT = 100

SAVE_FORMAT_VERSION = 1


class Direction(str, Enum):
    N = "N"
    E = "E"
    S = "S"
    W = "W"

    def delta(self) -> tuple[int, int]:
        if self == Direction.N:
            return (0, -1)
        if self == Direction.E:
            return (1, 0)
        if self == Direction.S:
            return (0, 1)
        return (-1, 0)

    def opposite(self) -> "Direction":
        if self == Direction.N:
            return Direction.S
        if self == Direction.E:
            return Direction.W
        if self == Direction.S:
            return Direction.N
        return Direction.E

    def turn_left(self) -> "Direction":
        if self == Direction.N:
            return Direction.W
        if self == Direction.W:
            return Direction.S
        if self == Direction.S:
            return Direction.E
        return Direction.N

    def turn_right(self) -> "Direction":
        if self == Direction.N:
            return Direction.E
        if self == Direction.E:
            return Direction.S
        if self == Direction.S:
            return Direction.W
        return Direction.N


def parse_direction(value: str | Direction | None) -> Direction | None:
    if value is None:
        return None
    if isinstance(value, Direction):
        return value
    if isinstance(value, str):
        upper = value.upper()
        if upper in ("N", "E", "S", "W"):
            return Direction(upper)
    raise ValueError(f"Unknown direction: {value!r}")


class InserterState(str, Enum):
    IDLE = "IDLE"
    SWING_TO_DROP = "SWING_TO_DROP"
    WAIT_DROP = "WAIT_DROP"
    SWING_BACK = "SWING_BACK"


# Entity types and their sizes
ENTITY_SIZES: dict[str, tuple[int, int]] = {
    "belt": (1, 1),
    "inserter": (1, 1),
    "miner": (2, 2),
    "assembler": (3, 3),
    "chest": (1, 1),
    "pole": (1, 1),
    "generator": (2, 2),
}

DIRECTIONAL_ENTITIES: set[str] = {"belt", "inserter"}

POWER_CONSUMPTION: dict[str, int] = {
    "inserter": INSERTER_POWER,
    "miner": MINER_POWER,
    "assembler": ASSEMBLER_POWER,
}

POWER_PRODUCTION: dict[str, int] = {
    "generator": GENERATOR_POWER,
}


def is_valid_entity_type(entity_type: str) -> bool:
    return entity_type in ENTITY_SIZES


def get_entity_size(entity_type: str) -> tuple[int, int]:
    if entity_type not in ENTITY_SIZES:
        raise ValueError(f"Unknown entity type: {entity_type!r}")
    return ENTITY_SIZES[entity_type]


def needs_direction(entity_type: str) -> bool:
    return entity_type in DIRECTIONAL_ENTITIES


def get_power_consumption(entity_type: str) -> int:
    return POWER_CONSUMPTION.get(entity_type, 0)


def get_power_production(entity_type: str) -> int:
    return POWER_PRODUCTION.get(entity_type, 0)


@dataclass
class Recipe:
    recipe_id: str
    ingredients: dict[str, int]
    results: dict[str, int]
    time_ticks: int

    def total_progress(self) -> int:
        return self.time_ticks * SAT_ONE


RECIPES: dict[str, Recipe] = {
    "iron-plate": Recipe(
        recipe_id="iron-plate",
        ingredients={"iron-ore": 1},
        results={"iron-plate": 1},
        time_ticks=192,
    ),
    "gear": Recipe(
        recipe_id="gear",
        ingredients={"iron-plate": 2},
        results={"gear": 1},
        time_ticks=30,
    ),
}


def get_recipe(recipe_id: str) -> Recipe:
    if recipe_id not in RECIPES:
        raise ValueError(f"Unknown recipe: {recipe_id!r}")
    return RECIPES[recipe_id]