"""Give the RLS login roles their passwords, from the environment.

Migration 0011 creates `citiq_app` and `citiq_admin` with LOGIN but no
password, because a migration must never carry credentials: it is committed to
the repository and replayed in every environment. The consequence is that a
freshly migrated database has two roles that nothing can actually authenticate
as, so client-api (`DATABASE_URL_APP`) and worker (`DATABASE_URL_ADMIN`) fail
to connect until somebody runs ALTER ROLE by hand.

This closes that gap. admin-api runs it on every boot, right after
`alembic upgrade head`, using the superuser connection it already holds for
migrations. It is idempotent: setting a role's password to the value it
already has is a no-op.

Passwords come from CITIQ_APP_PASSWORD and CITIQ_ADMIN_PASSWORD. Either being
unset is not an error, it just skips that role, so local development (where
both engines fall back to the superuser URL) needs no extra configuration.
"""
import asyncio
import os

import structlog
from sqlalchemy import text

from app.db import engine

logger = structlog.get_logger()

# Role name -> environment variable holding its password.
ROLE_PASSWORD_VARS = {
    "citiq_app": "CITIQ_APP_PASSWORD",
    "citiq_admin": "CITIQ_ADMIN_PASSWORD",
}


async def bootstrap_roles() -> None:
    updated, skipped = [], []

    async with engine.begin() as conn:
        for role, var in ROLE_PASSWORD_VARS.items():
            password = os.environ.get(var, "").strip()
            if not password:
                skipped.append(role)
                continue

            exists = await conn.scalar(
                text("SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = :role"),
                {"role": role},
            )
            if not exists:
                # Migrations have not created it yet; nothing to do.
                skipped.append(role)
                continue

            # ALTER ROLE is DDL, and PostgreSQL does not accept bind
            # parameters in it, so the password has to reach the statement as
            # a literal. Let the server produce that literal with
            # quote_literal() (the password is still bound on the way in)
            # rather than escaping quotes by hand here.
            quoted = await conn.scalar(
                text("SELECT quote_literal(:password)"), {"password": password}
            )
            # The role name is a fixed key from the mapping above, never user
            # input, so interpolating it directly is safe.
            await conn.execute(text(f"ALTER ROLE {role} WITH PASSWORD {quoted}"))
            updated.append(role)

    # Never log the passwords themselves, only which roles were touched.
    logger.info("bootstrap_roles_complete", updated=updated, skipped=skipped)


if __name__ == "__main__":
    asyncio.run(bootstrap_roles())
