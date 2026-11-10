"""schedulebot core: a pure scheduling engine.

No I/O, no clock reads, no database, no HTTP. Everything here is deterministic
given (tasks, routines, busy, origin, now), which is what makes it testable in
milliseconds and safe to run inside any service.
"""

from .categories import (
    PRESET_CATEGORIES,
    Category,
    blocking_for,
    catalog,
    resolve,
)
from .domain import (
    Block,
    Context,
    Energy,
    Kind,
    Late,
    Occupied,
    Plan,
    Priority,
    Reason,
    Routine,
    Shortfall,
    Task,
    Window,
)
from .explain import explain
from .solver import solve, topological_order

__all__ = [
    "Block",
    "Category",
    "Context",
    "PRESET_CATEGORIES",
    "Energy",
    "Kind",
    "Late",
    "Occupied",
    "Plan",
    "Priority",
    "Reason",
    "Routine",
    "Shortfall",
    "Task",
    "Window",
    "blocking_for",
    "catalog",
    "explain",
    "resolve",
    "solve",
    "topological_order",
]
