"""The HTTP surface.

Three rules shape this file:

* **No registration.** There is no route that creates a user; accounts come from
  the CLI on the host.
* **Every route is scoped to the signed-in user.** The user id comes from the
  session, never from the request body, so no endpoint can be pointed at somebody
  else's tasks.
* **Handlers do plumbing only.** Scheduling lives in the engine, persistence in
  the store, explanation in `explain.py`. Nothing here decides anything.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from .. import views
from ..categories import Category
from ..domain import Plan, Task
from ..llm.contract import CaptureResult
from ..slots import SLOTS_PER_DAY, floor_slot, midnight, minutes_for
from ..solver import solve
from ..store import plans as plans_store
from ..store import tasks as tasks_store
from ..store import users as users_store
from ..store.db import Store
from ..store.users import User
from .models import CaptureIn, CategoryIn, LoginIn, ProgressIn, TaskIn
from .security import LoginThrottle

COOKIE = "schedulebot_session"
CaptureFn = Callable[..., CaptureResult]


def _task_json(task: Task) -> dict[str, object]:
    return {
        "id": task.id,
        "title": task.title,
        "minutes": task.minutes,
        "remaining": task.remaining,
        "category": task.category,
        "priority": int(task.priority),
        "energy": task.energy.value if task.energy else None,
        "kind": task.kind.value,
        "min_chunk": task.min_chunk,
        "max_chunk": task.max_chunk,
        "splittable": task.splittable,
        "windows": [[w.start_min, w.end_min] for w in task.windows],
        "not_before": task.not_before.isoformat() if task.not_before else None,
        "due": task.due.isoformat() if task.due else None,
        "depends_on": list(task.depends_on),
        "max_per_day": task.max_per_day,
        "period_days": task.period_days,
        "context": {
            "people": list(task.context.people),
            "place": task.context.place,
            "requires": list(task.context.requires),
            "tags": list(task.context.tags),
            "notes": task.context.notes,
        },
    }


def _category_json(category: Category) -> dict[str, object]:
    return {
        "id": category.id,
        "name": category.name,
        "colour": category.colour,
        "energy": category.energy.value if category.energy else None,
        "windows": [[w.start_min, w.end_min] for w in category.windows],
        "blocking": list(category.blocking),
        "description": category.description,
    }


def create_app(
    store: Store,
    *,
    capture: CaptureFn | None = None,
    secure_cookies: bool = False,
    horizon_days: int = 21,
    session_ttl_days: int = 30,
    throttle: LoginThrottle | None = None,
) -> FastAPI:
    app = FastAPI(title="schedulebot", version="0.1.0", docs_url=None, redoc_url=None)
    app.state.store = store
    app.state.capture = capture
    limiter = throttle or LoginThrottle()

    # -- helpers ---------------------------------------------------------

    def current_user(request: Request) -> User:
        user = users_store.user_for_token(store, request.cookies.get(COOKIE, ""))
        if user is None:
            raise HTTPException(status_code=401, detail="not signed in")
        return user

    def now_for(user: User) -> datetime:
        try:
            zone = ZoneInfo(user.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            zone = ZoneInfo("UTC")
        return datetime.now(zone)

    def categories_for(user: User) -> dict[str, Category]:
        loaded = tasks_store.load_categories(store, user.id)
        return loaded

    def solve_for(user: User) -> tuple[Plan, list[str], dict[str, Category]]:
        categories = categories_for(user)
        now = now_for(user)
        origin = midnight(now)
        horizon_slots = horizon_days * SLOTS_PER_DAY
        now_slot = max(0, floor_slot(now, origin))
        plan = solve(
            tasks_store.load_tasks(store, user.id),
            tasks_store.load_routines(store, user.id),
            (),
            origin=origin,
            horizon_slots=horizon_slots,
            now_slot=now_slot,
            previous=plans_store.load(store, user.id)[0],
            categories=categories,
        )
        warnings: list[str] = []
        titles = {task.id: task.title for task in tasks_store.load_tasks(store, user.id)}
        for shortfall in plan.shortfalls:
            warnings.append(
                f"{titles.get(shortfall.task_id, shortfall.task_id)}: only "
                f"{minutes_for(shortfall.placed_slots)} of "
                f"{minutes_for(shortfall.required_slots)} minutes could be placed"
            )
        for late in plan.late:
            warnings.append(
                f"{titles.get(late.task_id, late.task_id)}: finishes "
                f"{minutes_for(late.placed_end_slot - late.due_slot)} minutes after its deadline"
            )
        plans_store.save(store, user.id, plan, warnings=warnings)
        return plan, warnings, categories

    # -- auth ------------------------------------------------------------

    @app.post("/api/login", status_code=204)
    def login(body: LoginIn, request: Request, response: Response) -> Response:
        address = request.client.host if request.client else "-"
        key = f"{body.username.strip().lower()}|{address}"
        if limiter.blocked(key):
            raise HTTPException(
                status_code=429,
                detail="too many attempts, try again later",
                headers={"Retry-After": str(limiter.retry_after(key))},
            )
        user = users_store.verify_credentials(store, body.username, body.password)
        if user is None:
            limiter.fail(key)
            raise HTTPException(status_code=401, detail="invalid credentials")
        limiter.succeed(key)
        tasks_store.seed_categories(store, user.id)
        token = users_store.create_session(
            store, user.id, ttl=timedelta(days=session_ttl_days)
        )
        response.set_cookie(
            COOKIE,
            token,
            max_age=session_ttl_days * 86400,
            httponly=True,
            samesite="lax",
            secure=secure_cookies,
            path="/",
        )
        response.status_code = 204
        return response

    @app.post("/api/logout", status_code=204)
    def logout(request: Request, response: Response) -> Response:
        token = request.cookies.get(COOKIE, "")
        if token:
            users_store.revoke_session(store, token)
        response.delete_cookie(COOKIE, path="/")
        response.status_code = 204
        return response

    @app.get("/api/me")
    def me(user: User = Depends(current_user)) -> dict[str, object]:
        return {
            "username": user.username,
            "timezone": user.timezone,
            "categories": [_category_json(c) for c in categories_for(user).values()],
        }

    # -- categories ------------------------------------------------------

    @app.put("/api/categories/{category_id}")
    def put_category(
        category_id: str, body: CategoryIn, user: User = Depends(current_user)
    ) -> dict[str, object]:
        category = body.to_category(category_id)
        tasks_store.save_category(store, user.id, category)
        return _category_json(category)

    # -- tasks -----------------------------------------------------------

    @app.get("/api/tasks")
    def list_tasks(user: User = Depends(current_user)) -> list[dict[str, object]]:
        return [_task_json(task) for task in tasks_store.load_tasks(store, user.id)]

    @app.put("/api/tasks/{task_id}")
    def put_task(
        task_id: str, body: TaskIn, user: User = Depends(current_user)
    ) -> dict[str, object]:
        task = body.to_task(task_id)
        tasks_store.save_task(store, user.id, task)
        return _task_json(task)

    @app.delete("/api/tasks/{task_id}", status_code=204)
    def delete_task(task_id: str, user: User = Depends(current_user)) -> Response:
        if not tasks_store.delete_task(store, user.id, task_id):
            raise HTTPException(status_code=404, detail="no such task")
        return Response(status_code=204)

    @app.post("/api/tasks/{task_id}/progress")
    def post_progress(
        task_id: str, body: ProgressIn, user: User = Depends(current_user)
    ) -> dict[str, object]:
        task = tasks_store.record_progress(store, user.id, task_id, remaining=body.remaining)
        if task is None:
            raise HTTPException(status_code=404, detail="no such task")
        return _task_json(task)

    # -- capture ---------------------------------------------------------

    @app.post("/api/capture")
    def post_capture(
        body: CaptureIn, user: User = Depends(current_user)
    ) -> dict[str, object]:
        if capture is None:
            raise HTTPException(status_code=503, detail="no model configured")
        categories = categories_for(user)
        try:
            result = capture(body.note, now=now_for(user), categories=categories)
        except Exception as exc:  # LlmError and anything the client raised
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        for task in result.tasks:
            tasks_store.save_task(
                store, user.id, task, reasoning=result.reasoning.get(task.id, "")
            )
        for routine in result.routines:
            tasks_store.save_routine(store, user.id, routine)
        plan, warnings, _ = solve_for(user)
        return {
            "created": [task.id for task in result.tasks],
            "routines": [routine.id for routine in result.routines],
            "reasoning": dict(result.reasoning),
            "warnings": [*result.warnings, *warnings],
            "plan": views.plan_view(
                plan,
                tasks=tasks_store.load_tasks(store, user.id),
                categories=categories,
                now_slot=max(0, floor_slot(now_for(user), plan.origin)),
            ),
        }

    # -- plan ------------------------------------------------------------

    @app.post("/api/plan/solve")
    def post_solve(user: User = Depends(current_user)) -> dict[str, object]:
        try:
            plan, warnings, categories = solve_for(user)
        except ValueError as exc:  # a dependency cycle, or a nonsense horizon
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        view = views.plan_view(
            plan,
            tasks=tasks_store.load_tasks(store, user.id),
            categories=categories,
            now_slot=max(0, floor_slot(now_for(user), plan.origin)),
        )
        view["warnings"] = warnings
        return view

    @app.get("/api/plan")
    def get_plan(user: User = Depends(current_user)) -> JSONResponse:
        plan, warnings, solved_at = plans_store.load(store, user.id)
        if plan is None:
            raise HTTPException(status_code=404, detail="no plan yet")
        view = views.plan_view(
            plan,
            tasks=tasks_store.load_tasks(store, user.id),
            categories=categories_for(user),
            now_slot=max(0, floor_slot(now_for(user), plan.origin)),
        )
        view["warnings"] = warnings
        view["solved_at"] = solved_at.isoformat() if solved_at else None
        return JSONResponse(view)

    @app.get("/api/now")
    def get_now(user: User = Depends(current_user)) -> dict[str, object]:
        plan, warnings, _ = plans_store.load(store, user.id)
        if plan is None:
            raise HTTPException(status_code=404, detail="no plan yet")
        current = views.now_view(
            plan,
            tasks=tasks_store.load_tasks(store, user.id),
            categories=categories_for(user),
            now_slot=max(0, floor_slot(now_for(user), plan.origin)),
        )
        if current is None:
            raise HTTPException(status_code=404, detail="nothing scheduled")
        current["warnings"] = warnings
        return current

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
