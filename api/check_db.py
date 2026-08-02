import asyncio
import os
import asyncpg


async def check():
    url = os.environ.get("DATABASE_URL", "")
    conn = await asyncpg.connect(url)

    r1 = await conn.fetch(
        "SELECT enumlabel FROM pg_enum "
        "JOIN pg_type ON pg_type.oid = pg_enum.enumtypid "
        "WHERE pg_type.typname = 'platform_type' ORDER BY enumsortorder"
    )
    print("platform_type enum values:", [r["enumlabel"] for r in r1])

    r2 = await conn.fetch("SELECT version_num FROM alembic_version")
    print("alembic_version:", [r["version_num"] for r in r2])

    await conn.close()


asyncio.run(check())
