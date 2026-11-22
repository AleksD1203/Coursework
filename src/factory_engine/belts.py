from collections import deque

from factory_engine.constants import (
    BELT_SPEED,
    DROP_POS,
    ITEM_SPACING,
    LEFT_LANE,
    MAX_POS,
    RIGHT_LANE,
    TILE_LEN,
)
from factory_engine.entities import Belt, BeltItem, Lane
from factory_engine.world import World


class BeltSystem:
    def __init__(self, speed: int = BELT_SPEED) -> None:
        if speed <= 0 or speed > ITEM_SPACING:
            raise ValueError(
                f"Belt speed must be between 1 and {ITEM_SPACING}: {speed}"
            )
        self.speed: int = speed
        self.update_order: list[Belt] = []
        self.cycle_count: int = 0
        self.sink_count: int = 0

    def rebuild_topology(self, world: World) -> None:
        belts = self._get_sorted_belts(world)
        incoming = self._connect_belts(world, belts)
        self.update_order = self._build_update_order(belts, incoming)
        world.belts_dirty = False

    def _get_sorted_belts(self, world: World) -> list[Belt]:
        raw = world.get_entities_of_type("belt")
        belts: list[Belt] = []
        for entity in raw:
            if isinstance(entity, Belt):
                belts.append(entity)
        return belts

    def _connect_belts(
        self,
        world: World,
        belts: list[Belt],
    ) -> dict[tuple[int, int], list[Belt]]:
        incoming: dict[tuple[int, int], list[Belt]] = {}
        for belt in belts:
            belt.next_belt = None
            belt.side_load_target_lane = None
            incoming[(belt.x, belt.y)] = []

        # Step 1: link each belt to the belt it points at
        for belt in belts:
            assert belt.direction is not None
            nx, ny = belt.next_cell()
            neighbor = world.get_belt_at(nx, ny)
            if neighbor is None:
                continue
            assert neighbor.direction is not None
            if belt.direction == neighbor.direction.opposite():
                continue
            belt.next_belt = neighbor
            incoming[(neighbor.x, neighbor.y)].append(belt)

        # Step 2: sort incoming lists and decide straight/curve vs side-loading
        for target in belts:
            assert target.direction is not None
            feeders = incoming[(target.x, target.y)]
            feeders.sort(key=lambda b: b.sort_key())

            has_straight_feeder = False
            for feeder in feeders:
                if feeder.direction == target.direction:
                    has_straight_feeder = True
                    break

            for feeder in feeders:
                assert feeder.direction is not None
                if feeder.direction == target.direction:
                    feeder.side_load_target_lane = None
                    continue

                if not has_straight_feeder and len(feeders) == 1:
                    feeder.side_load_target_lane = None
                elif feeder.direction == target.direction.turn_right():
                    feeder.side_load_target_lane = LEFT_LANE
                elif feeder.direction == target.direction.turn_left():
                    feeder.side_load_target_lane = RIGHT_LANE

        return incoming

    def _build_update_order(
        self,
        belts: list[Belt],
        incoming: dict[tuple[int, int], list[Belt]],
    ) -> list[Belt]:
        order: list[Belt] = []
        visited: set[tuple[int, int]] = set()
        self.sink_count = 0
        self.cycle_count = 0

        # 1. Start from sink belts (belts with no next_belt) and walk backwards
        for belt in belts:
            if belt.next_belt is None:
                self.sink_count += 1
                self._collect_reverse_bfs(belt, incoming, visited, order)

        # 2. Any unvisited belts belong to closed loops (or branches feeding loops)
        for belt in belts:
            coord = (belt.x, belt.y)
            if coord in visited:
                continue
            break_belt = self._find_cycle_break_point(belt)
            self.cycle_count += 1
            self._collect_reverse_bfs(break_belt, incoming, visited, order)

        return order

    def _collect_reverse_bfs(
        self,
        start: Belt,
        incoming: dict[tuple[int, int], list[Belt]],
        visited: set[tuple[int, int]],
        order: list[Belt],
    ) -> None:
        start_coord = (start.x, start.y)
        if start_coord in visited:
            return

        queue: deque[Belt] = deque([start])
        visited.add(start_coord)

        while queue:
            current = queue.popleft()
            order.append(current)
            for prev_belt in incoming[(current.x, current.y)]:
                prev_coord = (prev_belt.x, prev_belt.y)
                if prev_coord not in visited:
                    visited.add(prev_coord)
                    queue.append(prev_belt)

    def _find_cycle_break_point(self, start: Belt) -> Belt:
        path: list[Belt] = []
        index_by_coord: dict[tuple[int, int], int] = {}

        current: Belt | None = start
        while current is not None:
            coord = (current.x, current.y)
            if coord in index_by_coord:
                cycle_start = index_by_coord[coord]
                cycle_belts = path[cycle_start:]
                best = cycle_belts[0]
                for candidate in cycle_belts[1:]:
                    if candidate.sort_key() < best.sort_key():
                        best = candidate
                return best

            index_by_coord[coord] = len(path)
            path.append(current)
            current = current.next_belt

        return start

    def update_all(self, world: World) -> int:
        if world.belts_dirty:
            self.rebuild_topology(world)

        transitions = 0
        updated_coords: set[tuple[int, int]] = set()
        deferred_straight: list[tuple[Lane, BeltItem]] = []
        deferred_side_loads: list[tuple[Lane, Lane]] = []

        for belt in self.update_order:
            transitions += self._update_belt(
                belt,
                updated_coords,
                deferred_straight,
                deferred_side_loads,
            )
            updated_coords.add((belt.x, belt.y))

        for target_lane, item in deferred_straight:
            inserted = target_lane.insert_item(item)
            assert inserted

        for source_lane, target_lane in deferred_side_loads:
            front = source_lane.front_item()
            if front is not None and target_lane.can_insert_at(DROP_POS):
                taken = source_lane.take_front_item()
                assert taken is not None
                taken.pos = DROP_POS
                inserted = target_lane.insert_item(taken)
                assert inserted
                transitions += 1

        return transitions

    def _update_belt(
        self,
        belt: Belt,
        updated_coords: set[tuple[int, int]],
        deferred_straight: list[tuple[Lane, BeltItem]],
        deferred_side_loads: list[tuple[Lane, Lane]],
    ) -> int:
        transitions = 0
        next_belt = belt.next_belt

        if next_belt is None:
            transitions += self._advance_lane(
                belt.lanes[LEFT_LANE], None, False, deferred_straight
            )
            transitions += self._advance_lane(
                belt.lanes[RIGHT_LANE], None, False, deferred_straight
            )
            return transitions

        # Defer handoff if next_belt has not updated yet this tick (cycle break point)
        defer = (next_belt.x, next_belt.y) not in updated_coords

        if belt.side_load_target_lane is not None:
            target_lane = next_belt.lanes[belt.side_load_target_lane]
            transitions += self._advance_side_load_lane(
                belt.lanes[LEFT_LANE], target_lane, defer, deferred_side_loads
            )
            transitions += self._advance_side_load_lane(
                belt.lanes[RIGHT_LANE], target_lane, defer, deferred_side_loads
            )
            return transitions

        transitions += self._advance_lane(
            belt.lanes[LEFT_LANE], next_belt.lanes[LEFT_LANE], defer, deferred_straight
        )
        transitions += self._advance_lane(
            belt.lanes[RIGHT_LANE], next_belt.lanes[RIGHT_LANE], defer, deferred_straight
        )
        return transitions

    def _advance_side_load_lane(
        self,
        source_lane: Lane,
        target_lane: Lane,
        defer: bool,
        deferred_side_loads: list[tuple[Lane, Lane]],
    ) -> int:
        if source_lane.is_empty():
            return 0

        transitions = 0
        items = source_lane.items
        start_idx = 0

        front = items[0]
        wants_transfer = front.pos + self.speed >= TILE_LEN

        if wants_transfer and not defer and target_lane.can_insert_at(DROP_POS):
            front.pos = DROP_POS
            inserted = target_lane.insert_item(front)
            assert inserted
            start_idx = 1
            transitions = 1

        remaining: list[BeltItem] = []
        prev_pos: int | None = None

        for item in items[start_idx:]:
            limit = MAX_POS if prev_pos is None else prev_pos - ITEM_SPACING
            candidate_pos = min(item.pos + self.speed, limit)
            new_pos = max(item.pos, candidate_pos)
            item.pos = new_pos
            prev_pos = new_pos
            remaining.append(item)

        source_lane.items = remaining

        if wants_transfer and defer and transitions == 0:
            deferred_side_loads.append((source_lane, target_lane))

        return transitions

    def _advance_lane(
        self,
        source_lane: Lane,
        target_lane: Lane | None,
        defer: bool,
        deferred_straight: list[tuple[Lane, BeltItem]],
    ) -> int:
        if source_lane.is_empty():
            return 0

        remaining: list[BeltItem] = []
        transferred: list[BeltItem] = []
        prev_virtual_pos: int | None = None

        for item in source_lane.items:
            if prev_virtual_pos is None:
                limit = self._compute_front_limit(target_lane)
            else:
                limit = prev_virtual_pos - ITEM_SPACING

            # Never push an item backwards if a jam already exists ahead
            candidate_pos = min(item.pos + self.speed, limit)
            new_pos = max(item.pos, candidate_pos)
            prev_virtual_pos = new_pos

            if new_pos >= TILE_LEN:
                assert target_lane is not None
                item.pos = new_pos - TILE_LEN
                transferred.append(item)
            else:
                item.pos = new_pos
                remaining.append(item)

        source_lane.items = remaining
        if transferred:
            assert target_lane is not None
            if defer:
                for moved in transferred:
                    deferred_straight.append((target_lane, moved))
            else:
                target_lane.items.extend(transferred)

        return len(transferred)

    def _compute_front_limit(self, target_lane: Lane | None) -> int:
        if target_lane is None:
            return MAX_POS
        back_item = target_lane.back_item()
        if back_item is None:
            return TILE_LEN + MAX_POS
        return back_item.pos + TILE_LEN - ITEM_SPACING

    def get_all_belt_items(
        self,
        world: World,
        rect: tuple[int, int, int, int] | None = None,
    ) -> list[tuple[Belt, int, BeltItem]]:
        belts = world.query_entities(entity_type="belt", rect=rect)
        result: list[tuple[Belt, int, BeltItem]] = []
        for entity in belts:
            if not isinstance(entity, Belt):
                continue
            for lane_idx in (LEFT_LANE, RIGHT_LANE):
                for item in entity.lanes[lane_idx].items:
                    result.append((entity, lane_idx, item))
        return result

    def count_items(self, world: World) -> int:
        total = 0
        for entity in world.get_entities_of_type("belt"):
            if isinstance(entity, Belt):
                total += entity.item_count()
        return total

    def count_items_by_type(self, world: World) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entity in world.get_entities_of_type("belt"):
            if not isinstance(entity, Belt):
                continue
            for lane_idx in (LEFT_LANE, RIGHT_LANE):
                for item in entity.lanes[lane_idx].items:
                    counts[item.item_type] = counts.get(item.item_type, 0) + 1
        return counts

    def get_topology_stats(self, world: World) -> dict[str, int]:
        if world.belts_dirty:
            self.rebuild_topology(world)
        return {
            "belt_count": len(self.update_order),
            "sink_count": self.sink_count,
            "cycle_count": self.cycle_count,
        }

    def validate_all_belts(self, world: World) -> None:
        for entity in world.get_entities_of_type("belt"):
            if not isinstance(entity, Belt):
                continue
            entity.lanes[LEFT_LANE].validate_invariants()
            entity.lanes[RIGHT_LANE].validate_invariants()