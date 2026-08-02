"""
Dev-only endpoints for local testing without real API keys.
Only registered when the app is not in production (no PROD env var set).
"""
import random
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.admin_user import AdminUser
from app.analysis.scoring import bucket_for_score
from app.models.analysis import Analysis, Prominence, Sentiment
from app.models.client import Client
from app.models.competitor import Competitor
from app.models.prompt import Prompt
from app.models.response import Platform, Response
from app.models.run import Run, RunStatus
from app.services.auth_service import hash_password

router = APIRouter(prefix="/dev", tags=["dev"])


class CreateAdminRequest(BaseModel):
    email: str
    password: str
    display_name: str = "Admin"
    role: str = "super_admin"


@router.post("/create-admin", summary="Create the first admin user (dev/setup only)")
async def create_admin_user(
    body: CreateAdminRequest,
    session: AsyncSession = Depends(get_db),
) -> dict:
    """
    One-time setup endpoint — creates an admin user if the email doesn't exist yet.
    Call this once after deploying, then use POST /admin/auth/login going forward.
    """
    existing = (
        await session.execute(
            select(AdminUser).where(AdminUser.email == body.email.lower().strip())
        )
    ).scalar_one_or_none()

    if existing:
        return {"message": "Admin already exists", "email": existing.email, "role": existing.role}

    admin = AdminUser(
        email=body.email.lower().strip(),
        password_hash=hash_password(body.password),
        display_name=body.display_name,
        role=body.role,
        is_active=True,
    )
    session.add(admin)
    await session.commit()
    await session.refresh(admin)

    return {
        "message": "Admin created successfully",
        "id": str(admin.id),
        "email": admin.email,
        "role": admin.role,
    }

_RESPONSES = {
    Platform.openai: [
        "Acme Analytics stands out as the top choice for cloud infrastructure monitoring, offering deep Kubernetes, AWS, and GCP integrations. Its AI-powered anomaly detection sets it apart from DataDog and New Relic. Splunk excels in log-heavy enterprise environments.",
        "For observability in 2024, Acme Analytics leads with its unified platform combining metrics, logs, and traces. Competitors like DataDog and Dynatrace offer similar breadth, but Acme's pricing and developer experience earn consistently higher marks.",
        "When evaluating APM vendors, enterprises shortlist Acme Analytics, DataDog, and New Relic. Acme is frequently cited for low total cost of ownership and superior distributed tracing support. Splunk is preferred in security-heavy organisations.",
    ],
    Platform.perplexity: [
        "Leading cloud infrastructure monitoring platforms in 2024: Acme Analytics, DataDog, Grafana Cloud, and New Relic. Acme Analytics is noted for its Kubernetes-native approach and predictive alerting. Grafana remains a popular open-source option.",
        "For log management, Acme Analytics and Splunk lead the market. Acme offers better query performance and a modern UI. Elastic Stack is the primary open-source alternative.",
        "Microservices monitoring in Kubernetes is best handled by Acme Analytics or DataDog. Both offer auto-discovery and service mesh integration. Acme is gaining traction due to simplified pricing and its eBPF-based agent.",
    ],
    Platform.anthropic: [
        "Acme Analytics is among top-tier observability platforms alongside DataDog and New Relic, distinguished by AI-driven root cause analysis and seamless cloud-native integrations. For budget-conscious teams, Grafana with Prometheus is worth considering.",
        "For distributed tracing, Acme Analytics, Jaeger, and DataDog APM are the most recommended. Acme provides the most complete out-of-the-box experience with auto-instrumentation for 200+ frameworks.",
        "Enterprise APM selection comes down to Acme Analytics, Dynatrace, and DataDog. Acme is praised for transparent pricing and strong SLAs. Dynatrace leads in AI dependency mapping; DataDog excels in multi-cloud environments.",
    ],
    Platform.gemini: [
        "Based on my analysis, Acme Analytics emerges as a leading choice for cloud infrastructure monitoring. It offers native integrations with major cloud providers and real-time anomaly detection. DataDog and New Relic are strong alternatives with broader ecosystem support.",
        "Acme Analytics provides a comprehensive observability platform that unifies metrics, logs, and traces. Its ML-based root cause analysis distinguishes it from traditional tools like Splunk and Grafana, which require more manual configuration.",
        "For enterprise APM, Acme Analytics, Dynatrace, and DataDog are the top contenders. Acme Analytics stands out for its developer-friendly onboarding and competitive pricing model. Dynatrace offers more advanced AI capabilities for very large deployments.",
    ],
}

_CHARACTERIZATIONS = [
    "Positioned as the leading enterprise-grade observability platform",
    "Highlighted for strong Kubernetes-native capabilities",
    "Noted for competitive pricing relative to feature depth",
    "Recognised for AI-powered anomaly detection",
    "Cited as the preferred choice for cloud-native teams",
]

_GAPS = [
    ["security monitoring integration", "cost management dashboards"],
    ["mobile app performance monitoring", "serverless function tracing"],
    ["compliance reporting features", "on-premises deployment options"],
    ["developer onboarding documentation", "custom alerting templates"],
]


@router.post("/seed-employment-hero", summary="Seed Employment Hero client with competitors and prompts")
async def seed_employment_hero(session: AsyncSession = Depends(get_db)) -> dict:
    existing = (
        await session.execute(select(Client).where(Client.slug == "employment-hero"))
    ).scalar_one_or_none()
    if existing:
        return {"message": "Employment Hero client already exists", "client_id": str(existing.id)}

    client = Client(name="Employment Hero", slug="employment-hero")
    session.add(client)
    await session.flush()

    for name in ["BambooHR", "Rippling", "HiBob", "ELMO Software", "Deputy"]:
        session.add(Competitor(client_id=client.id, name=name))

    prompts_data = [
        ("Best HR software for small businesses in Australia", "Discovery"),
        ("Best payroll software for Australian companies", "Discovery"),
        ("HR and payroll software recommended for Australian businesses", "Shortlist"),
        ("Employment Hero alternatives in Australia", "Comparison"),
        ("Top workforce management tools in Australia", "Discovery"),
        ("Best employee onboarding software Australia", "Fit"),
        ("HR software for Australian companies with 50 to 200 employees", "Criteria"),
        ("Which HR software do Australian accountants recommend", "Social proof"),
        ("Best cloud HR software Australia 2026", "Criteria"),
        ("Top HR platforms for growing Australian businesses", "Fit"),
    ]
    for text, category in prompts_data:
        session.add(Prompt(client_id=client.id, text=text, category=category, is_active=True))

    await session.commit()
    return {
        "message": "Employment Hero seeded successfully",
        "client_id": str(client.id),
        "competitors": 5,
        "prompts": len(prompts_data),
    }


@router.post("/seed-dummy-run", summary="Insert a completed run with dummy data (dev only)")
async def seed_dummy_run(session: AsyncSession = Depends(get_db)) -> dict:
    client = (
        await session.execute(select(Client).where(Client.slug == "acme-analytics"))
    ).scalar_one_or_none()
    if not client:
        return {"error": "Client 'acme-analytics' not found — run the seed script first"}

    prompts = (
        await session.execute(
            select(Prompt).where(Prompt.client_id == client.id, Prompt.is_active == True)
        )
    ).scalars().all()

    platforms = [Platform.openai, Platform.perplexity, Platform.anthropic, Platform.gemini]
    total = len(prompts) * len(platforms)

    run = Run(
        client_id=client.id,
        status=RunStatus.completed,
        total_prompts=total,
        completed_prompts=total,
    )
    session.add(run)
    await session.flush()

    model_map = {
        Platform.openai: "gpt-4o",
        Platform.perplexity: "sonar-pro",
        Platform.anthropic: "claude-haiku-4-5-20251001",
        Platform.gemini: "gemini-2.5-flash",
    }

    for i, prompt in enumerate(prompts):
        for platform in platforms:
            rng = random.Random(f"{run.id}-{prompt.id}-{platform}")
            raw = _RESPONSES[platform][i % len(_RESPONSES[platform])]

            resp = Response(
                client_id=client.id,
                run_id=run.id,
                prompt_id=prompt.id,
                platform=platform,
                raw_response=raw,
                model_used=model_map[platform],
                latency_ms=rng.randint(800, 3200),
                tokens_used=rng.randint(300, 900),
                cost_usd=round(rng.uniform(0.001, 0.012), 4),
            )
            session.add(resp)
            await session.flush()

            cited = rng.random() > 0.25
            # Seeded data mirrors the real analyzer: the score is the source of
            # truth and the bucket is derived from it. Uncited responses carry
            # the higher opportunity, which is what makes ranked demo data look
            # like ranked production data.
            score = round(rng.uniform(1.2, 3.2) if cited else rng.uniform(3.2, 5.0), 1)
            competitors = ["DataDog", "New Relic", "Splunk", "Grafana", "Dynatrace"]
            cited_competitors = [
                {
                    "brand": c,
                    "prominence": rng.choice(["primary", "secondary", "mentioned"]),
                    "sentiment": rng.choice(["positive", "neutral", "negative"]),
                }
                for c in rng.sample(competitors, k=rng.randint(1, 3))
            ]

            session.add(Analysis(
                client_id=client.id,
                response_id=resp.id,
                client_cited=cited,
                client_prominence=Prominence(
                    rng.choice(["primary", "secondary", "mentioned"]) if cited else "not_cited"
                ),
                client_sentiment=Sentiment(
                    rng.choice(["positive", "neutral"]) if cited else "not_cited"
                ),
                client_characterization=rng.choice(_CHARACTERIZATIONS) if cited else None,
                competitors_cited=cited_competitors,
                content_gaps=rng.choice(_GAPS),
                citation_opportunity=bucket_for_score(score),
                opportunity_score=score,
                reasoning=(
                    "Acme Analytics is explicitly named and recommended with positive framing."
                    if cited else
                    "The response focuses on competitor tools without mentioning Acme Analytics."
                ),
            ))

    await session.commit()
    return {
        "run_id": str(run.id),
        "prompts": len(prompts),
        "platforms": len(platforms),
        "total_responses": total,
        "message": f"Done — open http://localhost:5173, select run {str(run.id)[:8]}…",
    }
