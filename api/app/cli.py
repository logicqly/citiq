"""
Admin CLI — create the first admin user without a registration endpoint.

Usage:
    python -m app.cli create-admin \
        --email admin@origolabs.ai \
        --password <secure-password> \
        --name "Admin User" \
        [--role super_admin]

Roles: super_admin | geo_lead | analyst (default: super_admin for CLI-created users)
"""
import argparse
import asyncio
import sys

from sqlalchemy import select

from app.db import AsyncSessionLocal
from app.models.admin_user import ADMIN_ROLES, AdminUser
from app.services.auth_service import hash_password


async def _create_admin(email: str, password: str, name: str, role: str) -> None:
    async with AsyncSessionLocal() as session:
        existing = (
            await session.execute(
                select(AdminUser).where(AdminUser.email == email.lower().strip())
            )
        ).scalar_one_or_none()

        if existing:
            print(f"ERROR: Admin with email '{email}' already exists.", file=sys.stderr)
            sys.exit(1)

        admin = AdminUser(
            email=email.lower().strip(),
            password_hash=hash_password(password),
            display_name=name,
            role=role,
            is_active=True,
        )
        session.add(admin)
        await session.commit()
        await session.refresh(admin)
        print(f"Admin created: id={admin.id}  email={admin.email}  role={admin.role}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Origo admin CLI")
    sub = parser.add_subparsers(dest="command")

    create = sub.add_parser("create-admin", help="Create an admin user")
    create.add_argument("--email", required=True)
    create.add_argument("--password", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--role", default="super_admin", choices=ADMIN_ROLES)

    args = parser.parse_args()

    if args.command == "create-admin":
        asyncio.run(_create_admin(args.email, args.password, args.name, args.role))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
