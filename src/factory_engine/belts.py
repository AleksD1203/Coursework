from collections import deque

from factory_engine.constants import (
    DROP_POS,
    ITEM_SPACING,
    MAX_POS,
    TILE_LEN,
    EntityType,
    LaneSide,
    Rect,
    parse_rect,
)
from factory_engine.entities import (
    Belt,
    BeltItem,
    BeltLink,
    DeadEndLink,
    Lane,
    SideLoadLink,
    StraightLink,
)
from factory_engine.world import World


class BeltSystem:
    """Two-phase conveyor solver (largest consistent advance) with side-loading."""

    def __init__(self) -> None:
        self.update_order: list[Belt] = []
        self._side_load_belts: list[Belt] = []
        self._built_version: int = -1

    def rebuild_topology(self, world: World) -> None:
        belts = world.get_entities_of_class(EntityType.BELT, Belt)
        links, straight_incoming = self._compute_links(world, belts)
        for belt in belts:
            belt.link = links[(belt.x, belt.y)]

        self.update_order = self._build_update_order(belts, straight_incoming)
        self._side_load_belts = [
            belt for belt in belts if isinstance(belt.link, SideLoadLink)
        ]
        self._built_version = world.topology_version

    def _compute_links(
        self,
        world: World,
        belts: list[Belt],
    ) -> tuple[dict[tuple[int, int], BeltLink], dict[tuple[int, int], list[Belt]]]:
        links: dict[tuple[int, int], BeltLink] = {
            (belt.x, belt.y): DeadEndLink() for belt in belts
        }
        all_incoming: dict[tuple[int, int], list[Belt]] = {
            (belt.x, belt.y): [] for belt in belts
        }
        straight_incoming: dict[tuple[int, int], list[Belt]] = {
            (belt.x, belt.y): [] for belt in belts
        }

        for belt in belts:
            nx, ny = belt.next_cell()
            neighbor = world.get_belt_at(nx, ny)
            if neighbor is None or belt.direction == neighbor.direction.opposite():
                continue
            all_incoming[(neighbor.x, neighbor.y)].append(belt)

        for target in belts:
            feeders = all_incoming[(target.x, target.y)]
            for feeder in feeders:
                if feeder.direction == target.direction or len(feeders) == 1:
                    links[(feeder.x, feeder.y)] = StraightLink(target)
                    straight_incoming[(target.x, target.y)].append(feeder)
                elif feeder.direction == target.direction.turn_right():
                    links[(feeder.x, feeder.y)] = SideLoadLink(target, LaneSide.LEFT)
                else:
                    links[(feeder.x, feeder.y)] = SideLoadLink(target, LaneSide.RIGHT)

        return links, straight_incoming

    def _build_update_order(
        self,
        belts: list[Belt],
        straight_incoming: dict[tuple[int, int], list[Belt]],
    ) -> list[Belt]:
        order: list[Belt] = []
        visited: set[tuple[int, int]] = set()

        for belt in belts:
            if not isinstance(belt.link, StraightLink):
                self._collect_reverse_bfs(belt, straight_incoming, visited, order)

        for belt in belts:
            if (belt.x, belt.y) in visited:
                continue
            break_belt = self._find_cycle_break_point(belt)
            self._collect_reverse_bfs(break_belt, straight_incoming, visited, order)

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
                cycle_belts = path[index_by_coord[coord] :]
                return min(cycle_belts, key=lambda b: b.sort_key())

            index_by_coord[coord] = len(path)
            path.append(current)
            current = current.link.target if isinstance(current.link, StraightLink) else None

        return start

    def update_all(self, world: World) -> None:
        if self._built_version != world.topology_version:
            self.rebuild_topology(world)

        active_lanes, straight_target = self._collect_active_lanes(set())
        moves, items_by_lane = self._solve_straight_moves(active_lanes, straight_target)

        admitted_side_loads: list[tuple[Lane, Lane, BeltItem]] = []
        excluded_fronts: set[Lane] = set()

        if self._side_load_belts:
            admitted_side_loads = self._admit_side_loads(
                moves, items_by_lane, straight_target
            )
            if admitted_side_loads:
                # Re-solve without admitted front items so streams behind a side-load do not lag.
                excluded_fronts = {src for src, _, _ in admitted_side_loads}
                active_lanes, straight_target = self._collect_active_lanes(
                    excluded_fronts
                )
                moves, items_by_lane = self._solve_straight_moves(
                    active_lanes, straight_target
                )

        self._apply_straight_moves(
            active_lanes, moves, straight_target, excluded_fronts
        )

        for _, target_lane, front in admitted_side_loads:
            moved = BeltItem(uid=front.uid, item_type=front.item_type, pos=DROP_POS)
            if not target_lane.insert_item(moved):
                raise RuntimeError("Admitted side-load violated target lane spacing")

    def _collect_active_lanes(
        self, excluded_fronts: set[Lane]
    ) -> tuple[list[tuple[Belt, Lane, tuple[BeltItem, ...]]], dict[Lane, Lane]]:
        active_lanes: list[tuple[Belt, Lane, tuple[BeltItem, ...]]] = []
        straight_target: dict[Lane, Lane] = {}

        for belt in self.update_order:
            target_belt = (
                belt.link.target if isinstance(belt.link, StraightLink) else None
            )
            for side in (LaneSide.LEFT, LaneSide.RIGHT):
                lane = belt.lanes[side.index]
                items = lane.items[1:] if lane in excluded_fronts else lane.items
                if not items:
                    continue
                active_lanes.append((belt, lane, items))
                if target_belt is not None:
                    straight_target[lane] = target_belt.lanes[side.index]

        return active_lanes, straight_target

    def _solve_straight_moves(
        self,
        active_lanes: list[tuple[Belt, Lane, tuple[BeltItem, ...]]],
        straight_target: dict[Lane, Lane],
    ) -> tuple[dict[Lane, list[int]], dict[Lane, tuple[BeltItem, ...]]]:
        """Compute the largest consistent advance for every item on straight/curved links."""
        moves: dict[Lane, list[int]] = {
            lane: [belt.speed] * len(items) for belt, lane, items in active_lanes
        }
        items_by_lane: dict[Lane, tuple[BeltItem, ...]] = {
            lane: items for _, lane, items in active_lanes
        }

        changed = True
        while changed:
            changed = False
            for _, lane, items in active_lanes:
                lane_moves = moves[lane]
                target_lane = straight_target.get(lane)
                front_pos = items[0].pos

                if target_lane is None:
                    max_front = MAX_POS - front_pos
                    if max_front < lane_moves[0]:
                        lane_moves[0] = max_front
                        changed = True
                else:
                    target_items = items_by_lane.get(target_lane)
                    if target_items is not None:
                        target_back_end = target_items[-1].pos + moves[target_lane][-1]
                        max_front = max(
                            0, TILE_LEN + target_back_end - ITEM_SPACING - front_pos
                        )
                        if max_front < lane_moves[0]:
                            lane_moves[0] = max_front
                            changed = True

                for idx in range(1, len(items)):
                    ahead_end = items[idx - 1].pos + lane_moves[idx - 1]
                    max_move = max(0, ahead_end - ITEM_SPACING - items[idx].pos)
                    if max_move < lane_moves[idx]:
                        lane_moves[idx] = max_move
                        changed = True

        return moves, items_by_lane

    def _admit_side_loads(
        self,
        moves: dict[Lane, list[int]],
        items_by_lane: dict[Lane, tuple[BeltItem, ...]],
        straight_target: dict[Lane, Lane],
    ) -> list[tuple[Lane, Lane, BeltItem]]:
        straight_feeder: dict[Lane, Lane] = {
            target: source for source, target in straight_target.items()
        }
        tentative_pos_cache: dict[Lane, list[int]] = {}
        admitted: list[tuple[Lane, Lane, BeltItem]] = []

        for belt in self._side_load_belts:
            assert isinstance(belt.link, SideLoadLink)
            target_lane = belt.link.target.get_lane(belt.link.target_side)

            for side in (LaneSide.LEFT, LaneSide.RIGHT):
                source_lane = belt.lanes[side.index]
                front = source_lane.front_item()
                if front is None or front.pos + belt.speed < TILE_LEN:
                    continue

                target_positions = tentative_pos_cache.get(target_lane)
                if target_positions is None:
                    target_positions = self._tentative_lane_positions(
                        target_lane, moves, items_by_lane, straight_feeder
                    )
                    tentative_pos_cache[target_lane] = target_positions

                if all(abs(pos - DROP_POS) >= ITEM_SPACING for pos in target_positions):
                    admitted.append((source_lane, target_lane, front))
                    target_positions.append(DROP_POS)

        return admitted

    def _tentative_lane_positions(
        self,
        target_lane: Lane,
        moves: dict[Lane, list[int]],
        items_by_lane: dict[Lane, tuple[BeltItem, ...]],
        straight_feeder: dict[Lane, Lane],
    ) -> list[int]:
        positions: list[int] = []
        target_items = items_by_lane.get(target_lane)
        if target_items is not None:
            for item, mv in zip(target_items, moves[target_lane]):
                new_pos = item.pos + mv
                if new_pos < TILE_LEN:
                    positions.append(new_pos)

        feeder_lane = straight_feeder.get(target_lane)
        if feeder_lane is not None:
            for item, mv in zip(items_by_lane[feeder_lane], moves[feeder_lane]):
                new_pos = item.pos + mv
                if new_pos >= TILE_LEN:
                    positions.append(new_pos - TILE_LEN)

        return positions

    def _apply_straight_moves(
        self,
        active_lanes: list[tuple[Belt, Lane, tuple[BeltItem, ...]]],
        moves: dict[Lane, list[int]],
        straight_target: dict[Lane, Lane],
        excluded_fronts: set[Lane],
    ) -> None:
        staying_by_lane: dict[Lane, list[BeltItem]] = {}
        incoming_by_lane: dict[Lane, list[BeltItem]] = {}

        for _, lane, items in active_lanes:
            staying: list[BeltItem] = []
            transferred: list[BeltItem] = []
            for item, mv in zip(items, moves[lane]):
                new_pos = item.pos + mv
                if new_pos >= TILE_LEN:
                    transferred.append(
                        BeltItem(
                            uid=item.uid,
                            item_type=item.item_type,
                            pos=new_pos - TILE_LEN,
                        )
                    )
                else:
                    staying.append(
                        BeltItem(uid=item.uid, item_type=item.item_type, pos=new_pos)
                    )

            staying_by_lane[lane] = staying
            if transferred:
                target_lane = straight_target[lane]
                incoming_by_lane[target_lane] = transferred

        touched = set(staying_by_lane) | set(incoming_by_lane) | excluded_fronts
        for belt in self.update_order:
            for lane in belt.lanes:
                if lane in touched:
                    lane.replace_items(
                        staying_by_lane.get(lane, []) + incoming_by_lane.get(lane, [])
                    )

    def get_all_belt_items(
        self,
        world: World,
        rect: Rect | tuple[int, int, int, int] | None = None,
    ) -> list[tuple[Belt, LaneSide, BeltItem]]:
        belts = world.get_entities_of_class(EntityType.BELT, Belt)
        parsed_rect = parse_rect(rect) if rect is not None else None
        result: list[tuple[Belt, LaneSide, BeltItem]] = []
        for belt in belts:
            if parsed_rect is not None and not parsed_rect.overlaps_box(
                belt.x, belt.y, belt.width, belt.height
            ):
                continue
            for side in (LaneSide.LEFT, LaneSide.RIGHT):
                for item in belt.lanes[side.index].items:
                    result.append((belt, side, item))
        return result

    def count_items_by_type(self, world: World) -> dict[str, int]:
        counts: dict[str, int] = {}
        for belt in world.get_entities_of_class(EntityType.BELT, Belt):
            for lane in belt.lanes:
                for item in lane.items:
                    counts[item.item_type] = counts.get(item.item_type, 0) + 1
        return counts

    def validate_all_belts(self, world: World) -> None:
        belts = world.get_entities_of_class(EntityType.BELT, Belt)
        expected_links, _ = self._compute_links(world, belts)

        for belt in belts:
            belt.lanes[0].validate_invariants()
            belt.lanes[1].validate_invariants()

            if self._built_version == world.topology_version:
                if belt.link != expected_links[(belt.x, belt.y)]:
                    raise ValueError(
                        f"Belt link cache stale at {(belt.x, belt.y)}: "
                        f"{belt.link} != {expected_links[(belt.x, belt.y)]}"
                    )
                if isinstance(belt.link, (StraightLink, SideLoadLink)):
                    target = belt.link.target
                    if world.get_belt_at(target.x, target.y) is not target:
                        raise ValueError(
                            f"Belt {(belt.x, belt.y)} links to removed belt {(target.x, target.y)}"
                        )

            expected = expected_links[(belt.x, belt.y)]
            if isinstance(expected, StraightLink):
                for side in (LaneSide.LEFT, LaneSide.RIGHT):
                    front = belt.lanes[side.index].front_item()
                    back = expected.target.lanes[side.index].back_item()
                    if front is not None and back is not None:
                        dist = (TILE_LEN - front.pos) + back.pos
                        if dist < ITEM_SPACING:
                            raise ValueError(
                                f"Cross-tile spacing violated from {(belt.x, belt.y)} "
                                f"to {(expected.target.x, expected.target.y)}: {dist}"
                            )