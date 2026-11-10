"""Operator CLI: create accounts, serve.

There is no registration endpoint. This is the other half of that decision —
creating a user has to be possible from the host, or the service is unusable.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from .store import tasks as tasks_store
from .store import users as users_store
from .store.db import Store


def _store(args: argparse.Namespace) -> Store:
    return Store(Path(args.db))


def _cmd_user_add(args: argparse.Namespace) -> int:
    store = _store(args)
    password = args.password
    if not password:
        password = getpass.getpass("password: ")
        if password != getpass.getpass("confirm: "):
            print("passwords do not match", file=sys.stderr)
            return 1
    try:
        user = users_store.create_user(
            store, args.username, password, timezone=args.timezone, is_admin=args.admin
        )
    except Exception as exc:
        print(f"could not create user: {exc}", file=sys.stderr)
        return 1
    tasks_store.seed_categories(store, user.id)
    print(f"created {user.username} ({user.timezone})")
    return 0


def _cmd_user_list(args: argparse.Namespace) -> int:
    store = _store(args)
    for user in users_store.list_users(store):
        flag = " (admin)" if user.is_admin else ""
        print(f"{user.username}\t{user.timezone}{flag}")
    return 0


def _cmd_user_passwd(args: argparse.Namespace) -> int:
    store = _store(args)
    row = store.one("SELECT id FROM users WHERE username = ?", (args.username,))
    if row is None:
        print("no such user", file=sys.stderr)
        return 1
    password = getpass.getpass("new password: ")
    try:
        users_store.set_password(store, str(row["id"]), password)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("password changed; existing sessions revoked")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .api.app import create_app
    from .llm.groq import GroqClient, GroqConfig

    store = _store(args)
    capture = None
    if args.llm_key:
        capture = GroqClient(
            GroqConfig(api_key=args.llm_key, model=args.llm_model, base_url=args.llm_base_url)
        ).capture
    app = create_app(store, capture=capture, secure_cookies=args.secure_cookies)
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="schedulebot")
    parser.add_argument("--db", default="/opt/schedulebot/data/schedulebot.db")
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("user-add", help="create an account")
    add.add_argument("username")
    add.add_argument("--password", default="")
    add.add_argument("--timezone", default="Europe/London")
    add.add_argument("--admin", action="store_true")
    add.set_defaults(func=_cmd_user_add)

    listing = sub.add_parser("user-list", help="list accounts")
    listing.set_defaults(func=_cmd_user_list)

    passwd = sub.add_parser("user-passwd", help="change a password")
    passwd.add_argument("username")
    passwd.set_defaults(func=_cmd_user_passwd)

    serve = sub.add_parser("serve", help="run the API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--llm-key", default="")
    serve.add_argument("--llm-model", default="qwen/qwen3.8-27b")
    serve.add_argument("--llm-base-url", default="https://api.groq.com/openai/v1")
    serve.add_argument("--secure-cookies", action="store_true")
    serve.add_argument("--log-level", default="info")
    serve.set_defaults(func=_cmd_serve)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
