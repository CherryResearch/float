"""Command line interface for managing sync destinations."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from app.services.sync_service import SyncService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage synchronization destinations",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    add_p = sub.add_parser("add-destination", help="Add a new destination")
    add_p.add_argument("name")
    add_p.add_argument("url")

    sub.add_parser("list-destinations", help="List configured destinations")

    rm_p = sub.add_parser("remove-destination", help="Remove a destination")
    rm_p.add_argument("name")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    secret = os.getenv("SYNC_SECRET", "cli-secret")
    config_path = Path(
        os.getenv("SYNC_CONFIG", "backend/config/sync_destinations.json")
    )
    service = SyncService(secret_key=secret, config_path=config_path)

    if args.command == "add-destination":
        service.add_destination(args.name, args.url)
    elif args.command == "list-destinations":
        for name, url in service.list_destinations().items():
            print(f"{name}: {url}")
    elif args.command == "remove-destination":
        service.remove_destination(args.name)


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    main()
