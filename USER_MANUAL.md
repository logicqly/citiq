# Citiq — User Manual

**Version:** 0.2.0  
**Last Updated:** May 2026  
**Product:** Citiq — GEO (Generative Engine Optimization) Monitoring Platform

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [How It Works — System Overview](#2-how-it-works--system-overview)
3. [Accessing the Platform](#3-accessing-the-platform)
4. [Client Dashboard Guide](#4-client-dashboard-guide)
   - 4.1 [Logging In](#41-logging-in)
   - 4.2 [Changing Your Password](#42-changing-your-password)
   - 4.3 [Dashboard Home](#43-dashboard-home)
   - 4.4 [Run History](#44-run-history)
   - 4.5 [Run Detail](#45-run-detail)
5. [Admin Dashboard Guide](#5-admin-dashboard-guide)
   - 5.1 [Logging In as Admin](#51-logging-in-as-admin)
   - 5.2 [Client List](#52-client-list)
   - 5.3 [Creating a Client](#53-creating-a-client)
   - 5.4 [Client Settings](#54-client-settings)
   - 5.5 [Managing Prompts](#55-managing-prompts)
   - 5.6 [Managing Competitors](#56-managing-competitors)
   - 5.7 [Knowledge Base](#57-knowledge-base)
   - 5.8 [Client Users](#58-client-users)
   - 5.9 [Scheduling Runs](#59-scheduling-runs)
   - 5.10 [Triggering a Run Manually](#510-triggering-a-run-manually)
   - 5.11 [Scheduler Health](#511-scheduler-health)
6. [Understanding Results](#6-understanding-results)
   - 6.1 [Visibility Score](#61-visibility-score)
   - 6.2 [Prominence Levels](#62-prominence-levels)
   - 6.3 [Sentiment](#63-sentiment)
   - 6.4 [Citation Opportunity](#64-citation-opportunity)
   - 6.5 [Content Gaps](#65-content-gaps)
   - 6.6 [Competitor Share of Voice](#66-competitor-share-of-voice)
7. [AI Platforms Covered](#7-ai-platforms-covered)
8. [Scheduling Reference](#8-scheduling-reference)
9. [Administrator Setup Guide](#9-administrator-setup-guide)
   - 9.1 [Environment Configuration](#91-environment-configuration)
   - 9.2 [Running the Stack](#92-running-the-stack)
   - 9.3 [Creating the First Admin Account](#93-creating-the-first-admin-account)
   - 9.4 [Applying Database Migrations](#94-applying-database-migrations)
   - 9.5 [Loading Seed / Demo Data](#95-loading-seed--demo-data)
10. [Troubleshooting](#10-troubleshooting)
11. [Glossary](#11-glossary)

---

## 1. Introduction

**Citiq** is a GEO (Generative Engine Optimization) monitoring platform. It answers a single critical question for your brand:

> *"When someone asks an AI assistant a question relevant to my industry, does it mention me — and how?"*

The platform works by automatically sending your curated set of questions to four major AI platforms simultaneously — **Perplexity**, **OpenAI (ChatGPT)**, **Anthropic (Claude)**, and **Google Gemini** — then using a dedicated analysis engine to evaluate every response for:

- Whether your brand was cited at all
- How prominently it was featured
- What sentiment was expressed toward it
- Which competitors were mentioned instead
- What topics the AI covered that your brand doesn't address (content gaps)

Results are collected into **Runs**, stored in full, and surfaced through two interfaces: a **Client Dashboard** for your brand team, and an **Admin Dashboard** for Logicqly internal staff.

---

## 2. How It Works — System Overview

```
Your Prompts
    │
    ▼
┌─────────────────────────────────────────────┐
│              Citiq API               │
│                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │Perplexity│  │  OpenAI  │  │ Anthropic│  │
│  └──────────┘  └──────────┘  └──────────┘  │
│                   ┌──────────┐              │
│                   │  Gemini  │              │
│                   └──────────┘              │
│                                             │
│         ▼  ▼  ▼  ▼  (responses)            │
│                                             │
│  ┌─────────────────────────────────────┐   │
│  │  Citation Analysis Engine (GPT-4o) │   │
│  │  - Cited? Prominence? Sentiment?   │   │
│  │  - Competitors mentioned?          │   │
│  │  - Content gaps?                   │   │
│  └─────────────────────────────────────┘   │
│                                             │
│         ▼  (results stored in DB)           │
└─────────────────────────────────────────────┘
    │
    ▼
┌───────────────┐     ┌────────────────────┐
│ Client        │     │ Admin Dashboard    │
│ Dashboard     │     │ (Logicqly staff only) │
└───────────────┘     └────────────────────┘
```

Each execution cycle is called a **Run**. Runs can be triggered manually by an admin, or scheduled automatically on hourly, daily, or weekly cadences.

---

## 3. Accessing the Platform

There are two separate web interfaces:

| Interface | Who Uses It | Typical URL |
|-----------|-------------|-------------|
| **Client Dashboard** | Your brand / marketing team | `https://your-app.railway.app` |
| **Admin Dashboard** | Logicqly internal staff | `https://your-app.railway.app/admin` |

> **Note:** Exact URLs are provided to you by your Logicqly account manager.

---

## 4. Client Dashboard Guide

### 4.1 Logging In

1. Navigate to your Client Dashboard URL.
2. Enter the **email** and **password** provided by your Logicqly account manager.
3. Click **Sign In**.

[SCREENSHOT: Client login page showing email/password fields and Sign In button]

> **First Login:** If this is your first time logging in, you will be redirected to a password change screen. You must set a new password before continuing.

**Login troubleshooting:**
- Passwords are case-sensitive.
- After 5 failed attempts within 15 minutes, your IP address will be temporarily blocked. Wait 15 minutes before retrying.
- Contact your Logicqly account manager if you are locked out.

---

### 4.2 Changing Your Password

On first login (or via your account settings), you will see a **Change Password** screen.

[SCREENSHOT: Change password page with current password, new password, and confirm password fields]

1. Enter your **current password**.
2. Enter a **new password** (minimum 8 characters recommended).
3. Re-enter the new password to confirm.
4. Click **Change Password**.

You will be redirected to the main dashboard immediately after.

---

### 4.3 Dashboard Home

The Dashboard Home is your primary view. It gives you an at-a-glance summary of your brand's AI visibility.

[SCREENSHOT: Dashboard Home showing visibility score, overview cards, and next scheduled run countdown]

**What you see:**

| Section | Description |
|---------|-------------|
| **Visibility Score** | A weighted score (0–100%) representing how consistently your brand appears across all AI platforms. Higher is better. |
| **Total Prompts** | The number of questions being monitored for your brand. |
| **Next Scheduled Run** | A countdown to when the next automated analysis will execute. |
| **Platform Breakdown Cards** | Citation rate per platform (Perplexity, OpenAI, Anthropic, Gemini) for the latest completed run. |
| **Competitor Mentions** | A quick view of which competitors appeared in the same responses as your brand queries. |
| **Prompts Table** | A per-question breakdown of where your brand was or wasn't cited. |

**Visibility Score interpretation:**

| Score Range | Interpretation |
|-------------|----------------|
| 75–100% | Strong visibility — your brand is cited consistently across platforms |
| 50–74% | Moderate visibility — room for improvement on some platforms |
| 25–49% | Weak visibility — significant gaps in brand citation |
| 0–24% | Very low visibility — your brand is rarely cited by AI assistants |

---

### 4.4 Run History

Click **Run History** in the left navigation to see all past runs.

[SCREENSHOT: Run History page showing a table of runs with dates, status badges, and citation rates]

Each row shows:
- **Run date and time** (in your account's configured timezone)
- **Status**: `Completed`, `Running`, `Failed`
- **Overall citation rate**: Percentage of responses across all prompts and platforms where your brand appeared
- A link to **View Details**

Runs are sorted newest first. Use the pagination controls at the bottom to browse older runs.

---

### 4.5 Run Detail

Clicking into any run opens the **Run Detail** page — your most in-depth view of results.

[SCREENSHOT: Run Detail page showing aggregated metrics at the top and per-prompt table below]

**The page is divided into two sections:**

#### Aggregated Metrics (top section)

| Metric | Description |
|--------|-------------|
| **Citation Rate** | % of all responses in this run where your brand was cited |
| **Per-Platform Citation Rate** | Breakdown by Perplexity / OpenAI / Anthropic / Gemini |
| **Sentiment Distribution** | How many responses were Positive / Neutral / Negative toward your brand |
| **Competitor Share of Voice** | Pie chart or table of how often each competitor appeared across responses |
| **Average Prominence** | Whether your brand appeared as a primary mention, secondary, or just a passing mention |

[SCREENSHOT: Aggregated metrics cards and competitor breakdown table]

#### Per-Prompt Breakdown (bottom section)

A table showing every prompt with columns for each AI platform. Each cell shows:
- Whether your brand was cited (✓ or ✗)
- Prominence level (e.g., Primary, Secondary, Mentioned)
- Sentiment icon (Positive, Neutral, Negative)
- Click any cell to expand the **full AI response** and the detailed analysis

[SCREENSHOT: Per-prompt breakdown table with expandable row showing full AI response text]

**Reading an expanded response:**
- **Raw Response**: The exact text returned by the AI platform
- **Client Citation**: Whether your brand appeared, and how it was characterized
- **Competitors Cited**: Other brands mentioned in the same response
- **Content Gaps**: Topics covered in the response that your brand's content doesn't address

---

## 5. Admin Dashboard Guide

The Admin Dashboard is for Logicqly internal staff only. It provides full control over clients, prompts, scheduling, and platform health.

### 5.1 Logging In as Admin

1. Navigate to the Admin Dashboard URL (typically `/admin` on the same domain).
2. Enter your admin email and password.
3. Click **Sign In**.

[SCREENSHOT: Admin login page]

Admin roles:

| Role | Access Level |
|------|-------------|
| **Super Admin** | Full access to all features |
| **GEO Lead** | Full client management, no admin user management |
| **Analyst** | Read-only access to client data and run results |

---

### 5.2 Client List

After login, you land on the **Client List** — a table of all clients on the platform.

[SCREENSHOT: Client list page showing table with client name, industry, status, prompt count, run count]

Each row shows:
- Client name and slug
- Industry
- Status badge: **Active**, **Paused**, or **Archived**
- Number of active prompts
- Number of completed runs

Click any client row to open that client's detail view.

---

### 5.3 Creating a Client

Click the **+ New Client** button (top right of the Client List) to open the Create Client modal.

[SCREENSHOT: Create Client modal with form fields]

**Required fields:**
- **Name**: The client's brand name (e.g., `Employment Hero`)
- **Slug**: A URL-safe identifier, auto-generated from the name (e.g., `employment-hero`)
- **Industry**: Free-text industry label

**Optional fields:**
- **Website**: Client's main website URL
- **Timezone**: IANA timezone for scheduling (e.g., `Australia/Sydney`). Defaults to `UTC` if not set.

Click **Create Client** to save. The client is created with `Active` status and you are taken to its detail page.

---

### 5.4 Client Settings

Inside a client's detail view, the **Settings** tab lets you edit the core client metadata.

[SCREENSHOT: Client settings tab with name, industry, website, timezone fields and a status badge]

**Changing client status:**
- **Active**: Client runs normally; scheduler will execute on schedule.
- **Paused**: Scheduler skips this client; manual runs are still possible.
- **Archived**: Client is hidden from active views; no runs execute.

Use the **Status** dropdown to change state. Changes take effect immediately.

---

### 5.5 Managing Prompts

The **Prompts** tab shows all questions configured for this client.

[SCREENSHOT: Prompts tab showing a table of prompts with category badges and active/inactive status]

**Adding a single prompt:**
1. Click **+ Add Prompt**.
2. Enter the question text (e.g., *"What is the best HR software for small businesses in Australia?"*).
3. Select a **Category**:

| Category | Purpose |
|----------|---------|
| `awareness` | Top-of-funnel questions about the space |
| `comparison` | "X vs Y" style comparisons |
| `intent` | Purchase or adoption intent questions |
| `how-to` | Process and how-to questions |
| `other` | Anything that doesn't fit above |

4. Click **Save**.

**Bulk adding prompts (JSON):**
1. Click **Bulk Add**.
2. Paste or upload a JSON array:
```json
[
  { "text": "What HR tools do Australian startups use?", "category": "awareness" },
  { "text": "Is Employment Hero better than Xero?", "category": "comparison" }
]
```
3. Click **Import**.

**Bulk adding prompts (CSV):**
1. Click **Upload CSV**.
2. Upload a `.csv` file with the columns `text` and `category`.
3. The system validates and imports all rows.

[SCREENSHOT: CSV upload dialog]

**Deactivating a prompt:**
Click the toggle on any prompt row to deactivate it. Inactive prompts are skipped in future runs but never deleted, preserving historical data.

---

### 5.6 Managing Competitors

The **Competitors** tab lists brands that the analysis engine watches for alongside your client.

[SCREENSHOT: Competitors tab with a list of competitor names and edit/delete actions]

When a competitor appears in an AI response to one of your client's prompts, it is recorded with its own prominence and sentiment, giving you a full **Share of Voice** view.

**Adding a competitor:**
1. Click **+ Add Competitor**.
2. Enter the competitor brand name (e.g., `Bamboo HR`).
3. Click **Save**.

**Bulk importing competitors:**
1. Click **Bulk Import**.
2. Supply a JSON array: `["Bamboo HR", "Rippling", "Deputy"]`
3. Click **Import**.

---

### 5.7 Knowledge Base

The **Knowledge Base** tab stores structured metadata about the client's brand. This context is available to the analysis engine and helps it assess citation quality.

[SCREENSHOT: Knowledge Base tab with four sections: Brand Profile, Target Audience, Brand Voice, Industry Context]

**Sections:**

| Section | What to Fill In |
|---------|----------------|
| **Brand Profile** | Core value proposition, key products/services, differentiators |
| **Target Audience** | Who the client serves (company size, geography, personas) |
| **Brand Voice** | Tone of voice, messaging pillars |
| **Industry Context** | Market landscape, regulatory context, trends |

Each section is free-form JSON or structured text. Click **Save Knowledge Base** after editing.

> The Knowledge Base is versioned. Every save creates a new version in the audit trail.

---

### 5.8 Client Users

The **Users** tab manages who on the client's team can log into the **Client Dashboard**.

[SCREENSHOT: Users tab with a table showing name, email, role, and status]

**Creating a user:**
1. Click **+ Add User**.
2. Enter **display name**, **email**, and initial **password**.
3. Select a **role**:
   - **Viewer**: Read-only access to the Client Dashboard.
   - **Editor**: Can interact with dashboard features (future use).
4. Click **Create User**.

The user will be prompted to change their password on first login.

**Resetting a password:**
Click the **Reset Password** button next to any user. You will set a new temporary password; the user must change it on next login.

**Deactivating a user:**
Click the **Deactivate** toggle. The user can no longer log in. Their historical data is preserved.

---

### 5.9 Scheduling Runs

The **Schedule** tab configures when runs execute automatically for this client.

[SCREENSHOT: Schedule tab showing cadence dropdown, hour/minute selectors, timezone display, and next run countdown]

**Cadences:**

| Cadence | Description |
|---------|-------------|
| **Manual** | Runs only when triggered manually by an admin. No automatic execution. |
| **Hourly** | Runs at the configured minute of every hour (e.g., `:30` = half past each hour). |
| **Daily** | Runs once per day at the configured hour and minute in the client's timezone. |
| **Weekly** | Runs once per week on the configured day and time. |

**Configuring a daily schedule (example):**
1. Set **Cadence** to `Daily`.
2. Set **Hour** to `8` and **Minute** to `0` for 8:00 AM.
3. Ensure the client's **Timezone** is correct (set in Client Settings).
4. Click **Save Schedule**.

The **Next Scheduled Run** timestamp updates immediately to reflect your changes.

**Pausing a schedule:**
Click **Pause Schedule** to temporarily stop automated runs. The schedule configuration is preserved. Click **Resume Schedule** to re-enable.

> **Emergency pause:** Admins can pause ALL clients at once from the Scheduler Health page. Use with care.

---

### 5.10 Triggering a Run Manually

From the **Runs** tab inside any client:

1. Click **Trigger New Run**.
2. The system immediately starts a run using all active prompts for this client.
3. The run status changes from `Running` → `Completed` (or `Failed`).

[SCREENSHOT: Runs tab with Trigger New Run button and a run in "Running" status with progress bar]

While a run is in progress, a **progress bar** shows how many prompts have been completed out of the total. The page auto-refreshes every few seconds.

> Runs are fully parallelized — all four AI platforms are queried simultaneously for each prompt. A typical run with 10 prompts completes in 30–60 seconds.

---

### 5.11 Scheduler Health

The **Scheduler Health** page (accessible from the top navigation) shows the global status of the automated scheduler.

[SCREENSHOT: Scheduler Health dashboard showing last tick time, runs today by status, and any failure alerts]

| Field | Description |
|-------|-------------|
| **Last Tick** | When the scheduler last checked for due runs (updates every 60 seconds) |
| **Tick Duration** | How long the last tick took to execute |
| **Clients Evaluated** | How many active clients were checked on the last tick |
| **Runs Enqueued Today** | How many scheduled runs were started today |
| **Consecutive Failures** | Count of back-to-back scheduler errors (0 is healthy) |
| **Last Error** | Details of the most recent failure, if any |

**Status indicators:**
- **Green / Healthy**: Scheduler is running normally, last tick was recent
- **Yellow / Warning**: Last tick was more than 5 minutes ago
- **Red / Critical**: Scheduler has stopped or is producing consecutive errors

**Emergency: Pause All Schedules**
If you need to halt all automated runs across all clients immediately, click **Pause All Schedules** on this page. This disables every client's schedule in a single operation. Re-enable schedules per client from each client's Schedule tab.

---

## 6. Understanding Results

### 6.1 Visibility Score

The **Visibility Score** is a single weighted number (0–100%) that summarises your brand's overall presence across all AI platforms and all monitored prompts.

It is calculated as:

```
Visibility Score = weighted average of per-prompt citation scores
                   across all active platforms
```

Higher-prominence citations contribute more to the score than passing mentions. This score is computed fresh after each run completes.

---

### 6.2 Prominence Levels

Every time your brand appears in an AI response, it is assigned a prominence level:

| Level | Meaning |
|-------|---------|
| **Primary** | Your brand is the main recommendation or central focus of the response |
| **Secondary** | Your brand is mentioned as a notable alternative or supporting example |
| **Mentioned** | Your brand appears briefly, listed among many options |
| **Not Cited** | Your brand does not appear in this response at all |

**Why this matters:** A "mentioned" citation in a list of 20 brands has very different marketing value than being the primary recommendation. Citiq tracks this distinction for every response.

---

### 6.3 Sentiment

Each citation is also assessed for sentiment:

| Sentiment | Meaning |
|-----------|---------|
| **Positive** | The AI expressed a favorable view of your brand (e.g., "highly recommended", "market leader") |
| **Neutral** | The AI mentioned your brand factually without positive or negative framing |
| **Negative** | The AI expressed concerns, caveats, or negative comparisons about your brand |

---

### 6.4 Citation Opportunity

The analysis engine scores each response from **1.0 to 5.0** for how much you stand to gain by acting on it. The score weighs everything the analysis found: whether you were cited, how prominently, the sentiment, the citation type, how your brand was described, which competitors were cited, and the content gaps.

| Score | Meaning |
|-------|---------|
| **4.5 - 5.0** | You are absent on a high-intent query, competitors are recommended by name, and the gaps are concrete — the strongest opportunity available |
| **3.8 - 4.4** | You are absent or barely mentioned while competitors hold the answer, with clear gaps to close |
| **3.0 - 3.7** | You appear but are not the recommended option, or are described vaguely — real but moderate upside |
| **2.0 - 2.9** | You are already cited positively and reasonably prominently; only reinforcement is available |
| **1.0 - 1.9** | You are the primary recommendation with positive sentiment and nothing meaningful is missing |

The decimal matters. Every response in a run is scored independently and then ranked against all the others, so the decimal is what separates two responses that fall in the same band. A run where everything scored a flat 5.0 could not be ranked at all.

A negative characterization raises the score even when your brand is prominently cited, because damage is worth correcting.

Every response in a run is ranked by this score, and the highest-scoring ones are what the recommendation engine works from. Responses analyzed before scoring was introduced show a High / Medium / Low level instead.

---

### 6.5 Content Gaps

The analysis engine identifies **topics covered in an AI response** that your brand's content doesn't currently address. These appear as a list of topics per response.

**Example content gap:** An AI response about "HR compliance software" discusses automatic award interpretation — if your brand's content doesn't cover this feature, it may be why the AI isn't citing you in that context.

Content gaps are your roadmap for improving AI citation coverage.

---

### 6.6 Competitor Share of Voice

The **Competitor Share of Voice** metric shows, across all responses in a run, what percentage of competitor mentions belonged to each tracked competitor.

**Example:**
- Bamboo HR: 42% of competitor mentions
- Rippling: 31%
- Deputy: 27%

This tells you which competitors are winning AI citations in your space and where the competitive pressure is greatest.

---

## 7. AI Platforms Covered

Citiq currently monitors four AI platforms simultaneously:

| Platform | Model Used | Notes |
|----------|-----------|-------|
| **Perplexity** | Perplexity Sonar | Web-connected AI; uses live search results |
| **OpenAI** | GPT-4o / GPT-4 Turbo | Standard chat model with broad knowledge |
| **Anthropic** | Claude 3.5 Sonnet / Opus | Strong reasoning and citation accuracy |
| **Google Gemini** | Gemini 1.5 Pro | Google's multi-modal model |

> All platforms are queried with the same prompt text so results are directly comparable.

**Platform errors:** Occasionally an individual platform may be unavailable or return an error. These are clearly flagged in the Run Detail view. Failed platform calls do not prevent the rest of the run from completing.

---

## 8. Scheduling Reference

### Schedule cadence options

| Cadence | Config Needed | Example |
|---------|--------------|---------|
| Manual | None | Admin triggers runs manually |
| Hourly | Minute (0–59) | Every hour at `:00` |
| Daily | Hour (0–23) + Minute | Every day at `08:00` local time |
| Weekly | Day of week + Hour + Minute | Every Monday at `09:00` |

### Timezone behavior

Schedules run in the **client's configured timezone** (set in Client Settings). For example, a client with `timezone = Australia/Sydney` configured for `Daily at 08:00` will run at 8:00 AM AEST — which is midnight UTC in summer, 10 PM UTC in winter.

### Retry behavior

If a scheduled run fails, the scheduler will automatically retry:
- **Attempt 2**: 5 minutes after failure
- **Attempt 3**: 10 minutes after failure
- **Attempt 4**: 20 minutes after failure

After 3 failed retries, the run is marked **Failed** and the scheduler moves on. Failed runs are visible in the client's Runs tab.

---

## 9. Administrator Setup Guide

> This section is for Logicqly engineering/ops staff who deploy and maintain the platform.

### 9.1 Environment Configuration

Copy `.env.example` to `.env` and populate all values:

```bash
cp .env.example .env
```

**Required environment variables:**

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string (e.g., `postgresql://user:pass@host:5432/db`) |
| `OPENAI_API_KEY` | OpenAI API key |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `PERPLEXITY_API_KEY` | Perplexity API key |
| `GEMINI_API_KEY` | Google Gemini API key |
| `JWT_SECRET_KEY` | Long random string for signing JWTs (generate with `openssl rand -hex 32`) |
| `REDIS_URL` | Redis connection string (e.g., `redis://localhost:6379`) |

**Optional environment variables:**

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `MAX_CONCURRENT_PER_PLATFORM` | `5` | Max parallel requests per AI platform |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Access token lifetime |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh token lifetime |
| `SCHEDULER_ENABLED` | `true` | Enable/disable the inline scheduler |

---

### 9.2 Running the Stack

**Development (Docker Compose):**
```bash
docker compose up --build
```

This starts three services:
- `db` — PostgreSQL 16 on port 5432
- `api` — FastAPI server on port 8000
- `web` — React client dashboard on port 5173

**Production:**
```bash
docker compose -f docker-compose.prod.yml up --build -d
```

**Running the API locally (without Docker):**
```bash
cd api
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000
```

**Running the client frontend locally:**
```bash
cd web
npm install
npm run dev
```

---

### 9.3 Creating the First Admin Account

Admin accounts cannot be created through the UI. Use the CLI:

```bash
cd api
python -m app.cli create-admin \
  --email admin@citiq.ai \
  --password "YourSecurePassword123" \
  --name "Admin Name" \
  --role super_admin
```

Available roles: `super_admin`, `geo_lead`, `analyst`

The CLI prints the new admin's user ID on success. You can then log in at the Admin Dashboard.

---

### 9.4 Applying Database Migrations

Migrations are managed with **Alembic**:

```bash
cd api
# Apply all pending migrations
alembic upgrade head

# Roll back one migration
alembic downgrade -1

# Create a new migration (after model changes)
alembic revision --autogenerate -m "describe_the_change"
```

Migrations run automatically on Docker Compose startup. For production Railway deployments, migrations are applied as a pre-deploy step.

---

### 9.5 Loading Seed / Demo Data

A seed file is provided for initial setup or demos:

```bash
cd api
python -m app.scripts.seed
```

This loads `seed_data.yaml` which creates:
- An **Acme Analytics** demo client
- 5 sample competitors
- 8 sample prompts covering multiple categories

An alternative seed file for Employment Hero use cases is available: `seed_employment_hero.yaml`. To use it, point the seed script at that file or rename it to `seed_data.yaml` before running.

---

## 10. Troubleshooting

### "I can't log in to the Client Dashboard"

1. Confirm you're using the correct URL for the **Client Dashboard** (not the Admin Dashboard).
2. Check that Caps Lock is off.
3. After 5 failed attempts, your IP is blocked for 15 minutes. Wait and retry.
4. Contact your Logicqly account manager to reset your password.

---

### "My run is stuck in 'Running' status"

This can happen if the API server restarted during a run. On next server startup, any run that has been `running` for more than 3 hours is automatically marked as `Failed`. If you need to recover faster:

1. Go to **Admin Dashboard → Client → Runs**.
2. Wait for the stale run to be cleaned up (next server restart).
3. Click **Trigger New Run** to start a fresh run.

---

### "The scheduler stopped running"

Check the **Scheduler Health** page:

1. Is **Last Tick** more than 2 minutes old? The scheduler may have crashed.
2. Are there **Consecutive Failures** > 0? Check the **Last Error** for details.
3. Is `SCHEDULER_ENABLED=true` in your environment variables?
4. Is Redis reachable? The scheduler uses a Redis lock; if Redis is down, no ticks fire.

To force a reset, restart the API server process. The scheduler restarts with the app.

---

### "A platform returned an error in my run"

Platform errors (e.g., API key expired, rate limit exceeded, platform downtime) are shown as error banners in the Run Detail view. The run still completes for all other platforms.

Check:
- Is the relevant API key valid and has remaining quota?
- Is the platform itself experiencing an outage?

Errors are logged to `api.log` with full details. For Railway deployments, use `railway logs` to view them.

---

### "Content gaps look wrong / citation analysis seems off"

The citation analysis uses GPT-4o-mini. Accuracy depends on:
- How clearly the brand is named in the prompt and knowledge base
- Whether the AI response actually contained brand-relevant content

If you see systematic errors, review the **Knowledge Base** for this client. Make sure the brand name is spelled exactly as it appears in AI responses (e.g., `Employment Hero` not `EmploymentHero`).

---

## 11. Glossary

| Term | Definition |
|------|-----------|
| **Run** | A single execution cycle: all active prompts sent to all platforms, all responses analyzed |
| **Prompt** | A question submitted to AI platforms (e.g., "What is the best HR software in Australia?") |
| **Citation** | An instance where your brand is mentioned in an AI response |
| **Prominence** | How central your brand is to an AI response (Primary / Secondary / Mentioned) |
| **Sentiment** | Whether the AI response was Positive, Neutral, or Negative toward your brand |
| **Citation Opportunity** | A score indicating how much potential there is to improve your citation in a given context |
| **Content Gap** | A topic covered in an AI response that your brand's content doesn't address |
| **Share of Voice** | The proportion of total competitor mentions attributable to each competitor |
| **Visibility Score** | A weighted aggregate score (0–100%) of your brand's overall AI citation performance |
| **GEO** | Generative Engine Optimization — the practice of optimizing brand presence in AI-generated content |
| **Cadence** | The frequency of scheduled runs (hourly, daily, weekly, manual) |
| **Knowledge Base** | Structured brand metadata (profile, audience, voice, context) stored per client |
| **Tenant** | A client organization; all data is fully isolated per tenant |
| **Admin Dashboard** | Logicqly-staff-only interface for managing clients, prompts, schedules, and users |
| **Client Dashboard** | Brand-team interface for viewing run results and visibility metrics |

---

*For support, contact your Logicqly account manager or raise an issue at the Logicqly internal tracker.*
