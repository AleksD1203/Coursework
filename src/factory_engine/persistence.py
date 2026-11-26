from collections.abc import Callable
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from factory_engine.constants import (
    MAX_SAVE_BYTES,
    MAX_SAVE_ENTITIES,
    MAX_WORLD_CELLS,
    MAX_WORLD_DIM,
    SAVE_FORMAT_VERSION,
    InserterState,
    LaneSide,
    needs_direction,
    parse_entity_type,
    require_strict_int,
    supports_tier,
    validate_item_name,
)
from factory_engine.entities import (
    Assembler,
    Belt,
    BeltItem,
    Chest,
    Deposit,
    Entity,
    HeldItem,
    Inserter,
    Lane,
    Miner,
    build_inserter_state,
    create_entity,
)
from factory_engine.world import World


class PersistenceError(Exception):
    """Raised when saving, loading, decoding, or validating world state fails."""


def _require_key(mapping: dict[str, Any], key: str, context: str) -> Any:
    if key not in mapping:
        raise ValueError(f"Missing required key {key!r} in {context}")
    return mapping[key]


def _require_dict(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"Expected object for {name}")
    return value


def _require_list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"Expected list for {name}")
    return value


def _serialize_lane(lane: Lane) -> list[dict[str, Any]]:
    return [
        {
            "uid": item.uid,
            "item_type": item.item_type,
            "pos": item.pos,
        }
        for item in lane.items
    ]


def _deserialize_lane(raw: Any, name: str, seen_uids: set[int]) -> Lane:
    raw_items = _require_list(raw, name)
    items: list[BeltItem] = []
    for i, entry in enumerate(raw_items):
        ctx = f"{name}[{i}]"
        item_data = _require_dict(entry, ctx)
        uid = require_strict_int(_require_key(item_data, "uid", ctx), f"{ctx}.uid")
        item_type = validate_item_name(_require_key(item_data, "item_type", ctx))
        pos = require_strict_int(_require_key(item_data, "pos", ctx), f"{ctx}.pos")

        if uid in seen_uids:
            raise ValueError(f"Duplicate item uid in save: {uid}")
        belt_item = BeltItem(uid=uid, item_type=item_type, pos=pos)
        seen_uids.add(uid)
        items.append(belt_item)

    return Lane(items)


def _serialize_entity(entity: Entity) -> dict[str, Any]:
    data: dict[str, Any] = {
        "type": entity.entity_type.value,
        "x": entity.x,
        "y": entity.y,
    }
    if entity.direction is not None:
        data["direction"] = entity.direction.value
    if supports_tier(entity.entity_type):
        data["tier"] = entity.tier

    if isinstance(entity, Belt):
        data["lanes"] = {
            LaneSide.LEFT.json_key: _serialize_lane(entity.lanes[LaneSide.LEFT.index]),
            LaneSide.RIGHT.json_key: _serialize_lane(entity.lanes[LaneSide.RIGHT.index]),
        }
    elif isinstance(entity, Inserter):
        data["state"] = entity.state.value
        data["held_item"] = entity.held.item_type if entity.held is not None else None
        data["held_uid"] = entity.held.uid if entity.held is not None else None
        data["progress"] = entity.progress
    elif isinstance(entity, Miner):
        data["progress"] = entity.progress
        data["output_buffer"] = entity.output_buffer.as_dict()
    elif isinstance(entity, Assembler):
        data["recipe_id"] = entity.recipe_id
        data["progress"] = entity.progress
        data["input_buffer"] = entity.input_buffer.as_dict()
        data["output_buffer"] = entity.output_buffer.as_dict()
    elif isinstance(entity, Chest):
        data["inventory"] = entity.inventory.as_dict()

    return data


def _deserialize_entity(raw: Any, seen_uids: set[int]) -> Entity:
    data = _require_dict(raw, "entity")
    entity_type = parse_entity_type(_require_key(data, "type", "entity"))
    x = require_strict_int(_require_key(data, "x", "entity"), "entity.x")
    y = require_strict_int(_require_key(data, "y", "entity"), "entity.y")
    direction = (
        _require_key(data, "direction", "entity")
        if needs_direction(entity_type)
        else data.get("direction")
    )
    tier = (
        require_strict_int(_require_key(data, "tier", "entity"), "entity.tier")
        if supports_tier(entity_type)
        else data.get("tier", 1)
    )

    entity = create_entity(entity_type, x, y, direction, tier=tier)

    if isinstance(entity, Belt):
        lanes_raw = _require_dict(_require_key(data, "lanes", "belt"), "belt.lanes")
        expected_keys = {LaneSide.LEFT.json_key, LaneSide.RIGHT.json_key}
        if set(lanes_raw.keys()) != expected_keys:
            raise ValueError(
                f"Belt lanes must have exact keys {sorted(expected_keys)}, got {sorted(lanes_raw.keys())}"
            )
        left_lane = _deserialize_lane(
            lanes_raw[LaneSide.LEFT.json_key], "belt.lanes.left", seen_uids
        )
        right_lane = _deserialize_lane(
            lanes_raw[LaneSide.RIGHT.json_key], "belt.lanes.right", seen_uids
        )
        entity.lanes = (left_lane, right_lane)

    elif isinstance(entity, Inserter):
        state_raw = _require_key(data, "state", "inserter")
        if not isinstance(state_raw, str):
            raise ValueError(f"Expected string for inserter.state, got {state_raw!r}")
        try:
            state_label = InserterState(state_raw)
        except ValueError as exc:
            raise ValueError(f"Unknown inserter state: {state_raw!r}") from exc

        held_item = _require_key(data, "held_item", "inserter")
        held_uid = _require_key(data, "held_uid", "inserter")
        if held_item is None and held_uid is None:
            held: HeldItem | None = None
        elif held_item is not None and held_uid is not None:
            uid = require_strict_int(held_uid, "inserter.held_uid")
            if uid in seen_uids:
                raise ValueError(f"Duplicate item uid in save: {uid}")
            held = HeldItem(uid=uid, item_type=validate_item_name(held_item))
            seen_uids.add(uid)
        else:
            raise ValueError("Inserter held_item and held_uid must both be set or both be null")

        progress = require_strict_int(
            _require_key(data, "progress", "inserter"), "inserter.progress"
        )
        entity.fsm_state = build_inserter_state(state_label, held, progress)

    elif isinstance(entity, Miner):
        progress = require_strict_int(
            _require_key(data, "progress", "miner"), "miner.progress"
        )
        if progress < 0:
            raise ValueError(f"Negative miner progress: {progress}")
        entity.progress = progress
        entity.output_buffer.load_counts(
            _require_dict(_require_key(data, "output_buffer", "miner"), "miner.output_buffer")
        )

    elif isinstance(entity, Assembler):
        recipe_id = _require_key(data, "recipe_id", "assembler")
        if recipe_id is not None:
            if not isinstance(recipe_id, str):
                raise ValueError(f"Expected string or null for assembler.recipe_id: {recipe_id!r}")
            entity.set_recipe(recipe_id)
        progress = require_strict_int(
            _require_key(data, "progress", "assembler"), "assembler.progress"
        )
        if progress < 0:
            raise ValueError(f"Negative assembler progress: {progress}")
        if recipe_id is None and progress != 0:
            raise ValueError("Assembler without recipe cannot have non-zero progress")
        entity.progress = progress
        entity.input_buffer.load_counts(
            _require_dict(_require_key(data, "input_buffer", "assembler"), "assembler.input_buffer")
        )
        entity.output_buffer.load_counts(
            _require_dict(
                _require_key(data, "output_buffer", "assembler"), "assembler.output_buffer"
            )
        )

    elif isinstance(entity, Chest):
        entity.inventory.load_counts(
            _require_dict(_require_key(data, "inventory", "chest"), "chest.inventory")
        )

    return entity


def world_to_dict(world: World) -> dict[str, Any]:
    entities_list = [_serialize_entity(entity) for entity in world.get_all_entities()]
    deposits_list = [
        {
            "x": deposit.x,
            "y": deposit.y,
            "item_type": deposit.item_type,
            "amount": deposit.amount,
        }
        for deposit in world.query_deposits()
    ]
    return {
        "version": SAVE_FORMAT_VERSION,
        "width": world.width,
        "height": world.height,
        "tick": world.tick,
        "next_item_uid": world.next_item_uid,
        "deposits": deposits_list,
        "entities": entities_list,
    }


def _parse_world_dict(data: Any) -> World:
    root = _require_dict(data, "save root")

    version = require_strict_int(_require_key(root, "version", "save root"), "version")
    if version != SAVE_FORMAT_VERSION:
        raise ValueError(
            f"Unsupported save version {version} (expected {SAVE_FORMAT_VERSION})"
        )

    width = require_strict_int(_require_key(root, "width", "save root"), "width")
    height = require_strict_int(_require_key(root, "height", "save root"), "height")
    tick = require_strict_int(_require_key(root, "tick", "save root"), "tick")
    next_item_uid = require_strict_int(
        _require_key(root, "next_item_uid", "save root"), "next_item_uid"
    )

    if width <= 0 or height <= 0 or width > MAX_WORLD_DIM or height > MAX_WORLD_DIM:
        raise ValueError(f"World dimensions out of allowed range 1..{MAX_WORLD_DIM}: {width}x{height}")
    if width * height > MAX_WORLD_CELLS:
        raise ValueError(f"World cell count {width * height} exceeds limit {MAX_WORLD_CELLS}")
    if tick < 0:
        raise ValueError(f"Tick cannot be negative: {tick}")
    if next_item_uid <= 0:
        raise ValueError(f"next_item_uid must be positive: {next_item_uid}")

    deposits_raw = _require_list(_require_key(root, "deposits", "save root"), "deposits")
    entities_raw = _require_list(_require_key(root, "entities", "save root"), "entities")
    if len(deposits_raw) > width * height:
        raise ValueError(f"Too many deposits in save: {len(deposits_raw)}")
    if len(entities_raw) > MAX_SAVE_ENTITIES:
        raise ValueError(f"Entity count {len(entities_raw)} exceeds limit {MAX_SAVE_ENTITIES}")

    world = World(width, height)
    world.tick = tick
    world.next_item_uid = next_item_uid

    for i, entry in enumerate(deposits_raw):
        ctx = f"deposits[{i}]"
        dep_data = _require_dict(entry, ctx)
        dx = require_strict_int(_require_key(dep_data, "x", ctx), f"{ctx}.x")
        dy = require_strict_int(_require_key(dep_data, "y", ctx), f"{ctx}.y")
        item_type = validate_item_name(_require_key(dep_data, "item_type", ctx))
        amount = require_strict_int(_require_key(dep_data, "amount", ctx), f"{ctx}.amount")

        if not world.in_bounds(dx, dy):
            raise ValueError(f"Deposit out of bounds: {(dx, dy)}")
        if (dx, dy) in world.deposits:
            raise ValueError(f"Duplicate deposit at {(dx, dy)}")

        world.deposits[(dx, dy)] = Deposit(x=dx, y=dy, item_type=item_type, amount=amount)

    seen_uids: set[int] = set()
    for entry in entities_raw:
        entity = _deserialize_entity(entry, seen_uids)
        if not world.add_entity(entity):
            raise ValueError(
                f"Cannot place {entity.entity_type.value} at {(entity.x, entity.y)} "
                f"(out of bounds or overlapping)"
            )

    if seen_uids and max(seen_uids) >= world.next_item_uid:
        raise ValueError(
            f"next_item_uid ({world.next_item_uid}) must be greater than "
            f"existing item uid ({max(seen_uids)})"
        )

    world.validate_spatial_index()
    return world


def world_from_dict(data: Any) -> World:
    try:
        return _parse_world_dict(data)
    except ValueError as exc:
        raise PersistenceError(f"Schema validation failed: {exc}") from exc


def world_to_json(world: World) -> str:
    return json.dumps(world_to_dict(world), sort_keys=True, separators=(",", ":"))


def world_from_json(raw_json: str) -> World:
    if not isinstance(raw_json, str):
        raise PersistenceError(f"Expected JSON string, got {type(raw_json).__name__}")
    if len(raw_json) > MAX_SAVE_BYTES:
        raise PersistenceError(f"Save payload exceeds maximum size of {MAX_SAVE_BYTES} bytes")
    try:
        parsed = json.loads(raw_json)
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise PersistenceError(f"JSON parsing failed: {exc}") from exc
    return world_from_dict(parsed)


def _write_text_fsync(path: Path, content: str) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _replace_file(src: Path, dst: Path) -> None:
    os.replace(src, dst)


def save_world_to_file(
    world: World,
    path: str | Path,
    *,
    write_fn: Callable[[Path, str], None] = _write_text_fsync,
    replace_fn: Callable[[Path, Path], None] = _replace_file,
) -> None:
    file_path = Path(path)
    payload = world_to_json(world)
    tmp_path: Path | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            dir=file_path.parent,
            prefix=f".{file_path.name}.",
            suffix=".tmp",
        )
        os.close(fd)
        tmp_path = Path(tmp_name)
        write_fn(tmp_path, payload)
        replace_fn(tmp_path, file_path)
    except OSError as exc:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise PersistenceError(f"Failed to save world to {file_path}: {exc}") from exc


def load_world_from_file(path: str | Path) -> World:
    file_path = Path(path)
    try:
        with file_path.open("rb") as handle:
            raw_bytes = handle.read(MAX_SAVE_BYTES + 1)
    except OSError as exc:
        raise PersistenceError(f"Failed to read save file {file_path}: {exc}") from exc

    if len(raw_bytes) > MAX_SAVE_BYTES:
        raise PersistenceError(f"Save file exceeds maximum size of {MAX_SAVE_BYTES} bytes")

    try:
        raw_json = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PersistenceError(f"Invalid UTF-8 in save file {file_path}: {exc}") from exc

    return world_from_json(raw_json)