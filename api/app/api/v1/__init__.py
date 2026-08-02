"""
Public /v1 Audit API — Milestone 1.

A thin, token-authenticated REST surface that wraps the existing Origo Engine
services so external automation can onboard a prospect, load their knowledge
base + prompts, run an audit, and pull full results without the admin UI.

This package is purely additive: it reuses the existing service layer
(run_orchestrator, pipeline, aggregator, prompt_service, report_service) and
never duplicates pipeline logic. Internal admin/JWT routes are untouched.
"""
