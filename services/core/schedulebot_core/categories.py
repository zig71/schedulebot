"""Categories: what kind of work this is, and the defaults that follow.

A category is not a label. It is the set of defaults that makes a task
schedulable without the user repeating themselves: when it should happen, how
much energy it takes, and which distraction block belongs with it.

Premade categories exist so a new account is useful on day one. They are
overridable per user — the presets are a starting point, not a schema.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .domain import Energy, Task, Window

#: Stable id used when a task has no category. Never sent to the model as a choice.
UNCATEGORISED = "uncategorised"


@dataclass(frozen=True, slots=True)
class Category:
    """A bucket with scheduling defaults.

    `blocking` names Cold Turkey blocks *the user created themselves*, and it is a
    list because a category is not one block: "no social, no video" is a different
    set of blocks from "no code, no docs". Cold Turkey blocklists are themselves
    flexible — sites and apps per block — so a category maps onto whichever sets
    of them the user already maintains.

    The app never invents a block name and never passes one the user did not
    configure. That is why this is a tuple of plain names checked against a local
    allowlist, not a path or a command.
    """

    id: str
    name: str
    colour: str
    energy: Energy | None = None
    windows: tuple[Window, ...] = ()
    blocking: tuple[str, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        if not self.id or not self.id.replace("-", "").isalnum():
            raise ValueError(f"category id {self.id!r} must be a slug")
        if not self.colour.startswith("#") or len(self.colour) not in (4, 7):
            raise ValueError(f"category {self.id}: colour must be a hex value")


#: Seven buckets that between them cover most of a week. Deliberately few: a
#: taxonomy nobody maintains is worse than none.
PRESET_CATEGORIES: tuple[Category, ...] = (
    Category(
        id="deep-work",
        name="Deep work",
        colour="#4C6FFF",
        energy=Energy.HIGH,
        windows=(Window(9 * 60, 12 * 60),),
        # Example mapping only: these are the user's own block names, changed in
        # settings. Nothing here is created or guessed by the app.
        blocking=("Deep work", "No social"),
        description="Focused, uninterrupted work that needs your best hours.",
    ),
    Category(
        id="learning",
        name="Learning",
        colour="#00A37A",
        energy=Energy.HIGH,
        windows=(Window(9 * 60, 12 * 60),),
        description="Study, courses, reading that has to be retained.",
    ),
    Category(
        id="admin",
        name="Admin",
        colour="#8A8F98",
        energy=Energy.MEDIUM,
        windows=(Window(14 * 60, 17 * 60),),
        description="Email, paperwork, scheduling, small errands.",
    ),
    Category(
        id="health",
        name="Health",
        colour="#E4572E",
        energy=Energy.MEDIUM,
        windows=(Window(7 * 60, 9 * 60), Window(17 * 60, 20 * 60)),
        description="Exercise, appointments, sleep-adjacent routines.",
    ),
    Category(
        id="chores",
        name="Chores",
        colour="#B08968",
        energy=Energy.LOW,
        windows=(Window(17 * 60, 20 * 60),),
        description="Household work that does not need a clear head.",
    ),
    Category(
        id="social",
        name="Social",
        colour="#B85C9E",
        energy=Energy.MEDIUM,
        windows=(Window(17 * 60, 21 * 60),),
        description="People, calls, anything with another human's calendar in it.",
    ),
    Category(
        id="recovery",
        name="Recovery",
        colour="#5AA9B8",
        energy=Energy.LOW,
        description="Rest that is scheduled on purpose, not time left over.",
    ),
)


def catalog(extra: tuple[Category, ...] = ()) -> dict[str, Category]:
    """Presets plus a user's own, with user categories able to override a preset id."""
    merged = {category.id: category for category in PRESET_CATEGORIES}
    for category in extra:
        merged[category.id] = category
    return merged


def resolve(task: Task, categories: dict[str, Category]) -> Task:
    """Fill a task's schedule defaults from its category.

    Only *absent* preferences are filled: a task that says "mornings" keeps
    mornings even when its category defaults to afternoons. Explicit always wins.
    """
    category = categories.get(task.category)
    if category is None or task.category == UNCATEGORISED:
        return task
    windows = task.windows if task.windows else category.windows
    energy = task.energy if task.energy is not None else category.energy
    if windows == task.windows and energy == task.energy:
        return task
    return replace(task, windows=windows, energy=energy)


def blocking_for(task: Task, categories: dict[str, Category]) -> tuple[str, ...]:
    """The distraction blocks that belong with this task, in the order to apply them."""
    category = categories.get(task.category)
    if category is None:
        return ()
    return category.blocking


def categorise(task: Task, categories: dict[str, Category]) -> str:
    """The display name for a task's category, falling back to the id."""
    category = categories.get(task.category)
    return category.name if category else task.category
