# Origo Engine — GEO Monitoring POC

A proof-of-concept pipeline that fans prompts out to Perplexity, OpenAI, and Anthropic,
analyzes brand citation in every response, and surfaces results in a live React dashboard.

## Quick start

### Prerequisites
- Docker Desktop (or Docker Engine + Compose v2)
- API keys for OpenAI, Anthropic, and Perplexity

### 1. Configure environment

```bash
cp .env.example .env
# Edit .env and fill in your API keys
```

### 2. Start the stack

```bash
docker compose up --build
```

This starts:
| Service | URL |
|---------|-----|
| API (FastAPI) | http://localhost:8000 |
| API docs | http://localhost:8000/docs |
| Web dashboard | http://localhost:5173 |
| PostgreSQL | localhost:5432 |

### 3. Seed the database

```bash
docker compose exec api python -m app.scripts.seed
```

### 4. Run a health check

```bash
curl http://localhost:8000/health
```

## Development

### Backend (outside Docker)

```bash
cd api
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# Run migrations
DATABASE_URL=postgresql+asyncpg://origo:origo_dev@localhost:5432/origo alembic upgrade head

# Start dev server
uvicorn app.main:app --reload
```

### Frontend (outside Docker)

```bash
cd web
npm install
npm run dev
```

### Running tests

```bash
cd api
pytest
```

Tests mock all external APIs — they never consume real credits.

## Project structure

```
origo-engine-poc/
├── docker-compose.yml       # One command to start everything
├── .env.example             # Copy to .env and fill in keys
├── seed_data.yaml           # Demo brand, prompts, competitors
├── api/                     # Python / FastAPI backend
│   ├── app/
│   │   ├── platforms/       # Perplexity / OpenAI / Anthropic adapters
│   │   ├── analysis/        # LLM-based citation analyzer
│   │   ├── services/        # Orchestrator + aggregator
│   │   └── api/             # FastAPI route handlers
│   └── alembic/             # DB migrations
└── web/                     # React + Vite frontend
    └── src/
        ├── components/      # Dashboard UI components
        └── lib/api.ts       # Typed API client
```

## Architecture notes

- **Tenant isolation**: every table has `client_id UUID NOT NULL`, indexed.
- **Append-only responses**: the `responses` table is never updated or deleted.
- **Exponential backoff**: all external API calls retry on 429/5xx with jitter.
- **Platform adapters**: adding a new AI platform is one new file in `platforms/`.
