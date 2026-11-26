from collections import deque

from factory_engine.constants import (
    POLE_SUPPLY_RADIUS,
    POWER_CONSUMER_TYPES,
    POWER_RELATED_TYPES,
    SATISFACTION_ONE,
    EntityType,
)
from factory_engine.entities import Entity, Generator, Pole
from factory_engine.world import World


class PowerNetwork:
    """Connected component of power poles with attached generators and consumers."""

    def __init__(self, network_id: int) -> None:
        self.network_id: int = network_id
        self.poles: list[Pole] = []
        self.generators: list[Generator] = []
        self.consumers: list[Entity] = []
        self.production_kw: int = 0
        self.consumption_kw: int = 0
        self.satisfaction: int = 0

    def recalculate(self) -> None:
        self.production_kw = sum(gen.power_production_kw() for gen in self.generators)
        self.consumption_kw = sum(
            consumer.power_consumption_kw() for consumer in self.consumers
        )
        if self.production_kw == 0:
            self.satisfaction = 0
        elif self.consumption_kw == 0 or self.production_kw >= self.consumption_kw:
            self.satisfaction = SATISFACTION_ONE
        else:
            self.satisfaction = (
                self.production_kw * SATISFACTION_ONE
            ) // self.consumption_kw


class PowerSystem:
    """BFS-based electrical network builder and cached satisfaction lookup."""

    def __init__(self) -> None:
        self.networks: dict[int, PowerNetwork] = {}
        self.wire_edges: list[tuple[tuple[int, int], tuple[int, int]]] = []
        self._built_version: int = -1

    def update(self, world: World) -> None:
        if self._built_version != world.topology_version:
            self.rebuild_networks(world)

    def rebuild_networks(self, world: World) -> None:
        self.networks.clear()
        self.wire_edges.clear()

        self._reset_entity_networks(world)
        poles = world.get_entities_of_class(EntityType.POLE, Pole)
        adjacency = self._build_pole_adjacency(world, poles)
        self._build_components_bfs(poles, adjacency)

        self._attach_generators(world)
        self._attach_consumers(world)

        for network in self.networks.values():
            network.generators.sort(key=lambda g: g.sort_key())
            network.consumers.sort(key=lambda c: c.sort_key())
            network.recalculate()

        self._built_version = world.topology_version

    def _reset_entity_networks(self, world: World) -> None:
        for entity_type in sorted(POWER_RELATED_TYPES):
            for entity in world.get_entities_of_type(entity_type):
                entity.network_id = None

    def _find_wire_neighbors(self, world: World, pole: Pole) -> list[Pole]:
        reach = pole.wire_reach
        candidates = world.iter_entities_in_box(
            pole.x - reach,
            pole.y - reach,
            pole.x + reach,
            pole.y + reach,
        )
        neighbors = [
            candidate
            for candidate in candidates
            if isinstance(candidate, Pole)
            and candidate is not pole
            and pole.in_wire_reach(candidate)
        ]
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
        candidates = world.iter_entities_in_box(
            entity.x - POLE_SUPPLY_RADIUS,
            entity.y - POLE_SUPPLY_RADIUS,
            entity.x + entity.width - 1 + POLE_SUPPLY_RADIUS,
            entity.y + entity.height - 1 + POLE_SUPPLY_RADIUS,
        )
        found_ids = {
            candidate.network_id
            for candidate in candidates
            if isinstance(candidate, Pole)
            and candidate.network_id is not None
            and candidate.covers_entity(entity)
        }
        return sorted(found_ids)

    def _attach_generators(self, world: World) -> None:
        for generator in world.get_entities_of_class(EntityType.GENERATOR, Generator):
            network_ids = self._find_covering_network_ids(world, generator)
            if not network_ids:
                continue
            chosen_id = network_ids[0]
            generator.network_id = chosen_id
            self.networks[chosen_id].generators.append(generator)

    def _attach_consumers(self, world: World) -> None:
        for entity_type in sorted(POWER_CONSUMER_TYPES):
            for entity in world.get_entities_of_type(entity_type):
                network_ids = self._find_covering_network_ids(world, entity)
                if not network_ids:
                    continue
                chosen_id = self._pick_consumer_network(network_ids)
                entity.network_id = chosen_id
                self.networks[chosen_id].consumers.append(entity)

    def _pick_consumer_network(self, network_ids: list[int]) -> int:
        # Every generator has positive output, so checking generators avoids a double totals pass.
        for net_id in network_ids:
            if self.networks[net_id].generators:
                return net_id
        return network_ids[0]

    def get_satisfaction_for_entity(self, entity: Entity) -> int:
        if entity.network_id is None:
            return 0
        network = self.networks.get(entity.network_id)
        if network is None:
            return 0
        return network.satisfaction

    def get_all_networks(self) -> list[PowerNetwork]:
        return [self.networks[net_id] for net_id in sorted(self.networks)]

    def get_wire_edges(self) -> list[tuple[tuple[int, int], tuple[int, int]]]:
        return list(self.wire_edges)

    def get_summary(self) -> dict[str, int]:
        return {
            "network_count": len(self.networks),
            "wire_count": len(self.wire_edges),
            "total_production_kw": sum(
                network.production_kw for network in self.networks.values()
            ),
            "total_consumption_kw": sum(
                network.consumption_kw for network in self.networks.values()
            ),
        }

    def validate_networks(self, world: World) -> None:
        poles = world.get_entities_of_class(EntityType.POLE, Pole)
        for pole in poles:
            if pole.network_id is None or pole.network_id not in self.networks:
                raise ValueError(
                    f"Pole at {(pole.x, pole.y)} has invalid network_id: {pole.network_id}"
                )

        total_network_poles = 0
        for net_id, network in self.networks.items():
            if network.network_id != net_id:
                raise ValueError(f"Network ID mismatch: {network.network_id} != {net_id}")
            if network.satisfaction < 0 or network.satisfaction > SATISFACTION_ONE:
                raise ValueError(f"Satisfaction out of bounds: {network.satisfaction}")
            total_network_poles += len(network.poles)

            for pole in network.poles:
                if pole.network_id != net_id or world.get_entity_at(pole.x, pole.y) is not pole:
                    raise ValueError(f"Stale or mismatched pole in network {net_id}")
            for gen in network.generators:
                if gen.network_id != net_id or world.get_entity_at(gen.x, gen.y) is not gen:
                    raise ValueError(f"Stale or mismatched generator in network {net_id}")
            for consumer in network.consumers:
                if (
                    consumer.network_id != net_id
                    or world.get_entity_at(consumer.x, consumer.y) is not consumer
                ):
                    raise ValueError(f"Stale or mismatched consumer in network {net_id}")

        if total_network_poles != len(poles):
            raise ValueError(
                f"Pole count mismatch: {total_network_poles} in networks vs {len(poles)} in world"
            )