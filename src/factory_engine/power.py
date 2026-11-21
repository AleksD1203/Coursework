from collections import deque

from factory_engine.constants import POLE_AREA, POLE_REACH, SAT_ONE
from factory_engine.entities import Entity, Generator, Pole
from factory_engine.world import World


CONSUMER_TYPES: tuple[str, ...] = ("miner", "assembler", "inserter")


class PowerNetwork:
    def __init__(self, network_id: int) -> None:
        self.network_id: int = network_id
        self.poles: list[Pole] = []
        self.generators: list[Generator] = []
        self.consumers: list[Entity] = []
        self.production_kw: int = 0
        self.consumption_kw: int = 0
        self.satisfaction: int = 0

    def recalculate_totals(self) -> None:
        total_prod = 0
        for gen in self.generators:
            total_prod += gen.power_production()

        total_cons = 0
        for consumer in self.consumers:
            total_cons += consumer.power_consumption()

        self.production_kw = total_prod
        self.consumption_kw = total_cons

    def compute_satisfaction(self) -> int:
        if self.production_kw <= 0:
            self.satisfaction = 0
            return self.satisfaction

        if self.consumption_kw <= 0:
            self.satisfaction = SAT_ONE
            return self.satisfaction

        if self.production_kw >= self.consumption_kw:
            self.satisfaction = SAT_ONE
        else:
            self.satisfaction = (self.production_kw * SAT_ONE) // self.consumption_kw

        return self.satisfaction

    def has_power(self) -> bool:
        return self.satisfaction > 0

    def entity_count(self) -> int:
        return len(self.poles) + len(self.generators) + len(self.consumers)

    def get_stats(self) -> dict[str, int]:
        return {
            "network_id": self.network_id,
            "poles": len(self.poles),
            "generators": len(self.generators),
            "consumers": len(self.consumers),
            "production_kw": self.production_kw,
            "consumption_kw": self.consumption_kw,
            "satisfaction": self.satisfaction,
        }


class PowerSystem:
    def __init__(self) -> None:
        self.networks: dict[int, PowerNetwork] = {}
        self.wire_edges: list[tuple[tuple[int, int], tuple[int, int]]] = []

    def update(self, world: World) -> None:
        if world.power_dirty:
            self.rebuild_networks(world)

        for network_id in sorted(self.networks.keys()):
            self.networks[network_id].compute_satisfaction()

    def rebuild_networks(self, world: World) -> None:
        self.networks.clear()
        self.wire_edges.clear()

        self._reset_entity_networks(world)
        poles = self._get_sorted_poles(world)
        adjacency = self._build_pole_adjacency(world, poles)
        self._build_components_bfs(poles, adjacency)

        self._attach_generators(world)
        for network in self.networks.values():
            network.recalculate_totals()

        self._attach_consumers(world)
        for network_id in sorted(self.networks.keys()):
            network = self.networks[network_id]
            network.recalculate_totals()
            network.compute_satisfaction()

        world.power_dirty = False

    def _reset_entity_networks(self, world: World) -> None:
        for entity_type in ("pole", "generator", "miner", "assembler", "inserter"):
            for entity in world.get_entities_of_type(entity_type):
                entity.network_id = None

    def _get_sorted_poles(self, world: World) -> list[Pole]:
        poles: list[Pole] = []
        for entity in world.get_entities_of_type("pole"):
            if isinstance(entity, Pole):
                poles.append(entity)
        return poles

    def _find_wire_neighbors(self, world: World, pole: Pole) -> list[Pole]:
        reach = pole.reach
        min_x = max(0, pole.x - reach)
        max_x = min(world.width - 1, pole.x + reach)
        min_y = max(0, pole.y - reach)
        max_y = min(world.height - 1, pole.y + reach)

        neighbors: list[Pole] = []
        for y in range(min_y, max_y + 1):
            for x in range(min_x, max_x + 1):
                if x == pole.x and y == pole.y:
                    continue
                candidate = world.get_entity_at(x, y)
                if isinstance(candidate, Pole) and pole.in_wire_reach(candidate):
                    neighbors.append(candidate)

        neighbors.sort(key=lambda p: p.sort_key())
        return neighbors

    def _build_pole_adjacency(
        self,
        world: World,
        poles: list[Pole],
    ) -> dict[tuple[int, int], list[Pole]]:
        adjacency: dict[tuple[int, int], list[Pole]] = {}
        edges_set: set[tuple[tuple[int, int], tuple[int, int]]] = set()

        for pole in poles:
            coord = (pole.x, pole.y)
            neighbors = self._find_wire_neighbors(world, pole)
            adjacency[coord] = neighbors
            for other in neighbors:
                other_coord = (other.x, other.y)
                if coord < other_coord:
                    edges_set.add((coord, other_coord))

        self.wire_edges = sorted(edges_set)
        return adjacency

    def _build_components_bfs(
        self,
        poles: list[Pole],
        adjacency: dict[tuple[int, int], list[Pole]],
    ) -> None:
        visited: set[tuple[int, int]] = set()
        next_network_id = 0

        for start_pole in poles:
            start_coord = (start_pole.x, start_pole.y)
            if start_coord in visited:
                continue

            network = PowerNetwork(next_network_id)
            queue: deque[Pole] = deque([start_pole])
            visited.add(start_coord)

            while queue:
                current = queue.popleft()
                current.network_id = next_network_id
                network.poles.append(current)

                for neighbor in adjacency[(current.x, current.y)]:
                    neighbor_coord = (neighbor.x, neighbor.y)
                    if neighbor_coord not in visited:
                        visited.add(neighbor_coord)
                        queue.append(neighbor)

            network.poles.sort(key=lambda p: p.sort_key())
            self.networks[next_network_id] = network
            next_network_id += 1

    def _find_covering_network_ids(self, world: World, entity: Entity) -> list[int]:
        min_x = max(0, entity.x - POLE_AREA)
        max_x = min(world.width - 1, entity.x + entity.width - 1 + POLE_AREA)
        min_y = max(0, entity.y - POLE_AREA)
        max_y = min(world.height - 1, entity.y + entity.height - 1 + POLE_AREA)

        found_ids: set[int] = set()
        for y in range(min_y, max_y + 1):
            for x in range(min_x, max_x + 1):
                candidate = world.get_entity_at(x, y)
                if isinstance(candidate, Pole) and candidate.network_id is not None:
                    if candidate.covers_entity(entity):
                        found_ids.add(candidate.network_id)

        return sorted(found_ids)

    def _attach_generators(self, world: World) -> None:
        for entity in world.get_entities_of_type("generator"):
            if not isinstance(entity, Generator):
                continue
            network_ids = self._find_covering_network_ids(world, entity)
            if not network_ids:
                continue
            chosen_id = network_ids[0]
            entity.network_id = chosen_id
            self.networks[chosen_id].generators.append(entity)

    def _attach_consumers(self, world: World) -> None:
        for entity_type in CONSUMER_TYPES:
            for entity in world.get_entities_of_type(entity_type):
                network_ids = self._find_covering_network_ids(world, entity)
                if not network_ids:
                    continue
                chosen_id = self._pick_consumer_network(network_ids)
                entity.network_id = chosen_id
                self.networks[chosen_id].consumers.append(entity)

    def _pick_consumer_network(self, network_ids: list[int]) -> int:
        for net_id in network_ids:
            if self.networks[net_id].production_kw > 0:
                return net_id
        return network_ids[0]

    def get_satisfaction_for_entity(self, entity: Entity) -> int:
        if entity.network_id is None:
            return 0
        network = self.networks.get(entity.network_id)
        if network is None:
            return 0
        return network.satisfaction

    def is_entity_powered(self, entity: Entity) -> bool:
        return self.get_satisfaction_for_entity(entity) > 0

    def get_network(self, network_id: int) -> PowerNetwork | None:
        return self.networks.get(network_id)

    def get_all_networks(self) -> list[PowerNetwork]:
        result: list[PowerNetwork] = []
        for net_id in sorted(self.networks.keys()):
            result.append(self.networks[net_id])
        return result

    def get_wire_edges(self) -> list[tuple[tuple[int, int], tuple[int, int]]]:
        return list(self.wire_edges)

    def get_total_production(self) -> int:
        total = 0
        for network in self.networks.values():
            total += network.production_kw
        return total

    def get_total_consumption(self) -> int:
        total = 0
        for network in self.networks.values():
            total += network.consumption_kw
        return total

    def get_summary(self) -> dict[str, int]:
        return {
            "network_count": len(self.networks),
            "wire_count": len(self.wire_edges),
            "total_production_kw": self.get_total_production(),
            "total_consumption_kw": self.get_total_consumption(),
        }

    def validate_networks(self, world: World) -> None:
        poles = self._get_sorted_poles(world)
        seen_poles = 0

        for pole in poles:
            if pole.network_id is None or pole.network_id not in self.networks:
                raise AssertionError(f"Pole at {(pole.x, pole.y)} has invalid network_id")
            seen_poles += 1

        total_network_poles = 0
        for net_id, network in self.networks.items():
            if network.network_id != net_id:
                raise AssertionError(f"Network ID mismatch: {net_id}")
            if network.satisfaction < 0 or network.satisfaction > SAT_ONE:
                raise AssertionError(f"Satisfaction out of bounds: {network.satisfaction}")
            total_network_poles += len(network.poles)

            for pole in network.poles:
                if pole.network_id != net_id:
                    raise AssertionError("Pole network_id mismatch")
            for gen in network.generators:
                if gen.network_id != net_id:
                    raise AssertionError("Generator network_id mismatch")
            for consumer in network.consumers:
                if consumer.network_id != net_id:
                    raise AssertionError("Consumer network_id mismatch")

        if total_network_poles != seen_poles:
            raise AssertionError("Pole count mismatch between world and power networks")