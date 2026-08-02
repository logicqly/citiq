"""
Seed script: loads seed_data.yaml into the database.

Usage (inside the api container or with DATABASE_URL set):
    python -m app.scripts.seed
    python -m app.scripts.seed --yaml /path/to/seed_data.yaml
"""
import argparse
import asyncio
import pathlib

import structlog
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import AsyncSessionLocal
from app.models.client import Client
from app.models.competitor import Competitor
from app.models.prompt import Prompt

logger = structlog.get_logger()

DEFAULT_YAML = pathlib.Path("/seed_data.yaml")
LOCAL_YAML = pathlib.Path(__file__).parents[3] / "seed_data.yaml"


def _find_yaml(override: str | None) -> pathlib.Path:
    if override:
        p = pathlib.Path(override)
        if not p.exists():
            raise FileNotFoundError(f"YAML not found: {p}")
        return p
    for candidate in (DEFAULT_YAML, LOCAL_YAML):
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "seed_data.yaml not found. Pass --yaml <path> or mount it at /seed_data.yaml"
    )


async def _upsert_client(session: AsyncSession, name: str, slug: str) -> Client:
    result = await session.execute(select(Client).where(Client.slug == slug))
    client = result.scalar_one_or_none()
    if client is None:
        client = Client(name=name, slug=slug)
        session.add(client)
        await session.flush()
        logger.info("seed_client_created", slug=slug)
    else:
        logger.info("seed_client_exists", slug=slug, client_id=str(client.id))
    return client


async def _seed_competitors(
    session: AsyncSession, client: Client, names: list[str]
) -> None:
    result = await session.execute(
        select(Competitor.name).where(Competitor.client_id == client.id)
    )
    existing = {row[0] for row in result.all()}
    new_names = [n for n in names if n not in existing]
    for name in new_names:
        session.add(Competitor(client_id=client.id, name=name))
    await session.flush()
    logger.info(
        "seed_competitors",
        client_id=str(client.id),
        added=len(new_names),
        skipped=len(names) - len(new_names),
    )


async def _seed_prompts(
    session: AsyncSession, client: Client, prompts: list[dict]
) -> None:
    result = await session.execute(
        select(Prompt.text).where(Prompt.client_id == client.id)
    )
    existing_texts = {row[0] for row in result.all()}
    added = 0
    for p in prompts:
        text = p["text"]
        if text not in existing_texts:
            session.add(
                Prompt(
                    client_id=client.id,
                    text=text,
                    category=p.get("category", ""),
                )
            )
            added += 1
    await session.flush()
    logger.info(
        "seed_prompts",
        client_id=str(client.id),
        added=added,
        skipped=len(prompts) - added,
    )


async def run_seed(yaml_path: str | None = None) -> None:
    path = _find_yaml(yaml_path)
    data = yaml.safe_load(path.read_text())

    async with AsyncSessionLocal() as session:
        async with session.begin():
            client = await _upsert_client(
                session,
                name=data["client"]["name"],
                slug=data["client"]["slug"],
            )
            await _seed_competitors(session, client, data.get("competitors", []))
            await _seed_prompts(session, client, data.get("prompts", []))

    logger.info("seed_complete", client_id=str(client.id), client_slug=client.slug)
    return client


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the Origo database from YAML")
    parser.add_argument("--yaml", help="Path to seed YAML file", default=None)
    args = parser.parse_args()
    asyncio.run(run_seed(args.yaml))


if __name__ == "__main__":
    main()
