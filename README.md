# Factory Simulation Engine

Детерміністичне headless-ядро симуляції виробничо-логістичних процесів (спрощений аналог Factorio) мовою Python без зовнішніх runtime-залежностей.

Детальна архітектура, алгоритми, API та стратегія тестування описані в **[DESIGN.md](DESIGN.md)**.

## Встановлення (Python 3.11+)

```bash
python -m venv .venv
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# Linux / macOS:
source .venv/bin/activate

pip install -e ".[dev]"
```

## Запуск тестів

```bash
# Усі юніт-, інтеграційні, property-based та benchmark тести
pytest

# Швидкий прогін без бенчмарків + branch coverage
pytest --benchmark-disable --cov=factory_engine --cov-report=term-missing

# Лише бенчмарки продуктивності (ціль: < 16.6 мс/тік)
pytest tests/test_benchmarks.py -v
```

## Мінімальний приклад використання

```python
from factory_engine import FactoryEngine

engine = FactoryEngine(20, 10)
engine.add_resource_patch(1, 2, 2, 2, "iron-ore", 50)
engine.place_entity("generator", 0, 6)
engine.place_entity("pole", 2, 4)
engine.place_entity("miner", 1, 2)

engine.tick(120)
snap = engine.snapshot()
print(f"Tick: {snap.tick}, entities: {len(snap.entities)}")
```