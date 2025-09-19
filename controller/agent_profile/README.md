# Agent profile assets

This directory contains reusable resources for priming an autonomous LLM agent that operates the Proxmox infrastructure through the Universal LLM Controller.

## Files
- `system_prompt.md` — canonical system message for the agent.
- `action_recipes.md` — quick command mapping cheat sheet.
- `__init__.py` — helper module exposing `get_agent_profile()` for FastAPI.

The FastAPI service exposes these assets at runtime via `GET /agent/profile` so that external orchestrators can fetch the latest prompt and defaults programmatically.
