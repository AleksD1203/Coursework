import json
from pathlib import Path
from typing import Any

from factory_engine.constants import (
    LEFT_LANE,
    RIGHT_LANE,
    SAVE_FORMAT_VERSION,
    InserterState,
)
from factory_engine.entities import (
    Assembler,
    Belt,
    BeltItem,
    Chest,
    Deposit,
    Entity,
    Inserter,
    Lane,
    Miner,
    create_entity,
)
from factory_engine.world import World


def _require_dict(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"Expected object for {name}")
    return value


def _require_list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"Expected list for {name}")
    return value


def _require_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Expected integer for {name}: {value!r}")
    return value


def _require_str(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"Expected non-empty string for {name}: {value!r}")
    return value


def _serialize_counts(counts: dict[str, int]) -> dict[str, int]:
    result: dict[str, int] = {}
    for key in sorted(counts.keys()):
        amount = counts[key]
        if amount > 0:
            result[key] = amount
    return result


def _deserialize_counts(raw: Any, name: str) -> dict[str, int]:
    data = _require_dict(raw, name)
    result: dict[str, int] = {}
    for key, val in data.items():
        item_type = _require_str(key, f"{name} key")
        amount = _require_int(val, f"{name}[{item_type}]")
        if amount < 0:
            raise ValueError(f"Negative item count in {name}: {amount}")
        if amount > 0:
            result[item_type] = amount
    return result


def _serialize_lane(lane: Lane) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in lane.items:
        items.append(
            {
                "uid": item.uid,
                "item_type": item.item_type,
                "pos": item.pos,
            }
        )
    return items


def _deserialize_lane(
    raw: Any,
    name: str,
    seen_uids: set[int],
) -> Lane:
    raw_items = _require_list(raw, name)
    items: list[BeltItem] = []
    for i, entry in enumerate(raw_items):
        item_data = _require_dict(entry, f"{name}[{i}]")
        uid = _require_int(item_data.get("uid"), f"{name}[{i}].uid")
        item_type = _require_str(item_data.get("item_type"), f"{name}[{i}].item_type")
        pos = _require_int(item_data.get("pos"), f"{name}[{i}].pos")

        if uid in seen_uids:
            raise ValueError(f"Duplicate item uid in save: {uid}")
        seen_uids.add(uid)

        items.append(BeltItem(uid=uid, item_type=item_type, pos=pos))

    return Lane(items)


def _serialize_entity(entity: Entity) -> dict[str, Any]:
    data: dict[str, Any] = {
        "type": entity.entity_type,
        "x": entity.x,
        "y": entity.y,
    }
    if entity.direction is not None:
        data["direction"] = entity.direction.value

    if isinstance(entity, Belt):
        data["lanes"] = [
            _serialize_lane(entity.lanes[LEFT_LANE]),
            _serialize_lane(entity.lanes[RIGHT_LANE]),
        ]
    elif isinstance(entity, Inserter):
        data["state"] = entity.state.value
        data["held_item"] = entity.held_item
        data["held_uid"] = entity.held_uid
        data["progress"] = entity.progress
    elif isinstance(entity, Miner):
        data["progress"] = entity.progress
        data["output_buffer"] = _serialize_counts(entity.output_buffer)
    elif isinstance(entity, Assembler):
        data["recipe_id"] = entity.recipe_id
        data["progress"] = entity.progress
        data["input_buffer"] = _serialize_counts(entity.input_buffer)
        data["output_buffer"] = _serialize_counts(entity.output_buffer)
    elif isinstance(entity, Chest):
        data["inventory"] = _serialize_counts(entity.inventory)

    return data


def _deserialize_entity(raw: Any, seen_uids: set[int]) -> Entity:
    data = _require_dict(raw, "entity")
    entity_type = _require_str(data.get("type"), "entity.type")
    x = _require_int(data.get("x"), "entity.x")
    y = _require_int(data.get("y"), "entity.y")
    direction = data.get("direction")

    entity = create_entity(entity_type, x, y, direction)

    if isinstance(entity, Belt):
        lanes_raw = _require_list(data.get("lanes", [[], []]), "belt.lanes")
        if len(lanes_raw) != 2:
            raise ValueError(f"Belt must have 2 lanes, got {len(lanes_raw)}")
        left_lane = _deserialize_lane(lanes_raw[0], "belt.lanes[0]", seen_uids)
        right_lane = _deserialize_lane(lanes_raw[1], "belt.lanes[1]", seen_uids)
        entity.lanes = (left_lane, right_lane)

    elif isinstance(entity, Inserter):
        state_str = _require_str(data.get("state", InserterState.IDLE.value), "inserter.state")
        try:
            entity.state = InserterState(state_str)
        except ValueError as exc:
            raise ValueError(f"Unknown inserter state: {state_str!r}") from exc

        held_item = data.get("held_item")
        held_uid = data.get("held_uid")
        if held_item is None:
            if held_uid is not None:
                raise ValueError("Inserter cannot have held_uid without held_item")
            entity.held_item = None
            entity.held_uid = None
        else:
            entity.held_item = _require_str(held_item, "inserter.held_item")
            uid = _require_int(held_uid, "inserter.held_uid")
            if uid < 0:
                raise ValueError(f"Invalid inserter held_uid: {uid}")
            if uid in seen_uids:
                raise ValueError(f"Duplicate item uid in save: {uid}")
            seen_uids.add(uid)
            entity.held_uid = uid

        progress = _require_int(data.get("progress", 0), "inserter.progress")
        if progress < 0:
            raise ValueError(f"Negative inserter progress: {progress}")
        entity.progress = progress

    elif isinstance(entity, Miner):
        progress = _require_int(data.get("progress", 0), "miner.progress")
        if progress < 0:
            raise ValueError(f"Negative miner progress: {progress}")
        entity.progress = progress
        entity.output_buffer = _deserialize_counts(
            data.get("output_buffer", {}), "miner.output_buffer"
        )

    elif isinstance(entity, Assembler):
        recipe_id = data.get("recipe_id")
        if recipe_id is not None:
            entity.set_recipe(_require_str(recipe_id, "assembler.recipe_id"))
        progress = _require_int(data.get("progress", 0), "assembler.progress")
        if progress < 0:
            raise ValueError(f"Negative assembler progress: {progress}")
        entity.progress = progress
        entity.input_buffer = _deserialize_counts(
            data.get("input_buffer", {}), "assembler.input_buffer"
        )
        entity.output_buffer = _deserialize_counts(
            data.get("output_buffer", {}), "assembler.output_buffer"
        )

    elif isinstance(entity, Chest):
        entity.inventory = _deserialize_counts(
            data.get("inventory", {}), "chest.inventory"
        )

    return entity


def world_to_dict(world: World) -> dict[str, Any]:
    entities_list = [_serialize_entity(e) for e in world.get_all_entities()]

    resources_list: list[dict[str, Any]] = []
    for coord in sorted(world.resources.keys()):
        deposit = world.resources[coord]
        if deposit.is_depleted():
            continue
        resources_list.append(
            {
                "x": deposit.x,
                "y": deposit.y,
                "resource_type": deposit.resource_type,
                "amount": deposit.amount,
            }
        )

    return {
        "version": SAVE_FORMAT_VERSION,
        "width": world.width,
        "height": world.height,
        "tick": world.tick,
        "next_item_uid": world.next_item_uid,
        "resources": resources_list,
        "entities": entities_list,
    }


def world_from_dict(data: dict[str, Any]) -> World:
    root = _require_dict(data, "save root")

    version = _require_int(root.get("version"), "version")
    if version != SAVE_FORMAT_VERSION:
        raise ValueError(
            f"Unsupported save version {version} (expected {SAVE_FORMAT_VERSION})"
        )

    width = _require_int(root.get("width"), "width")
    height = _require_int(root.get("height"), "height")
    tick = _require_int(root.get("tick", 0), "tick")
    next_item_uid = _require_int(root.get("next_item_uid", 1), "next_item_uid")

    if tick < 0:
        raise ValueError(f"Tick cannot be negative: {tick}")
    if next_item_uid <= 0:
        raise ValueError(f"next_item_uid must be positive: {next_item_uid}")

    world = World(width, height)
    world.tick = tick
    world.next_item_uid = next_item_uid

    resources_raw = _require_list(root.get("resources", []), "resources")
    for i, entry in enumerate(resources_raw):
        res_data = _require_dict(entry, f"resources[{i}]")
        rx = _require_int(res_data.get("x"), f"resources[{i}].x")
        ry = _require_int(res_data.get("y"), f"resources[{i}].y")
        res_type = _require_str(
            res_data.get("resource_type"), f"resources[{i}].resource_type"
        )
        amount = _require_int(res_data.get("amount"), f"resources[{i}].amount")

        if not world.in_bounds(rx, ry):
            raise ValueError(f"Resource out of bounds: {(rx, ry)}")
        if amount <= 0:
            raise ValueError(f"Resource amount must be positive: {amount}")
        if (rx, ry) in world.resources:
            raise ValueError(f"Duplicate resource deposit at {(rx, ry)}")

        world.resources[(rx, ry)] = Deposit(
            x=rx,
            y=ry,
            resource_type=res_type,
            amount=amount,
        )

    seen_uids: set[int] = set()
    entities_raw = _require_list(root.get("entities", []), "entities")
    for entry in entities_raw:
        entity = _deserialize_entity(entry, seen_uids)
        if not world.add_entity(entity):
            raise ValueError(
                f"Cannot place {entity.entity_type} at {(entity.x, entity.y)} "
                f"(out of bounds or overlapping)"
            )

    if seen_uids and max(seen_uids) >= world.next_item_uid:
        raise ValueError(
            f"next_item_uid ({world.next_item_uid}) must be greater than "
            f"existing item uid ({max(seen_uids)})"
        )

    world.belts_dirty = True
    world.power_dirty = True
    return world


def world_to_json(world: World) -> str:
    payload = world_to_dict(world)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def world_from_json(raw_json: str) -> World:
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON save data: {exc}") from exc
    return world_from_dict(parsed)


def save_world_to_file(world: World, path: str | Path) -> None:
    file_path = Path(path)
    file_path.write_text(world_to_json(world), encoding="utf-8")


def load_world_from_file(path: str | Path) -> World:
    file_path = Path(path)
    raw_json = file_path.read_text(encoding="utf-8")
    return world_from_json(raw_json)