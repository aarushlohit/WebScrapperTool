#!/usr/bin/env python3
"""
Production-grade Indian government opportunity intelligence engine.

This runner replaces the old single giant prompt with a staged pipeline:
bounded discovery tasks -> JSON salvage -> schema normalization -> live
validation -> deterministic export.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import selectors
import subprocess
import sys
import time
import threading
import itertools
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from http.client import IncompleteRead, HTTPException
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from coverage_intelligence import (
    CoverageIntelligenceEngine,
    SearchSaturationTracker,
    attach_consensus_to_event,
    consensus_strength,
)
from generate_final_cleared import build_final_cleared_file


PROJECT_DIR = Path(__file__).resolve().parent
OUTPUT_JSON = PROJECT_DIR / "hackathons_results.json"
CACHE_DIR = PROJECT_DIR / ".opportunity_cache"
COVERAGE_HISTORY_JSON = PROJECT_DIR / "coverage_history.json"

DEFAULT_MODELS = [
    "opencode/big-pickle",
    "opencode/hy3-preview-free",
    "opencode/minimax-m2.5-free",
]

MODEL_TIMEOUTS = {
    "opencode/big-pickle": 330,
    "opencode/hy3-preview-free": 330,
    "opencode/minimax-m2.5-free": 340,
    "cloudflare-workers-ai/@cf/moonshotai/kimi-k2.6": 330,
}

DEFAULT_MODEL_TIMEOUT = 330

MODEL_ALIASES = {
    "big-pickle": "opencode/big-pickle",
    "big_pickle": "opencode/big-pickle",
    "pickle": "opencode/big-pickle",
    "hy3": "opencode/hy3-preview-free",
    "hy3-preview": "opencode/hy3-preview-free",
    "hy3-preview-free": "opencode/hy3-preview-free",
    "minimax": "opencode/minimax-m2.5-free",
    "minimax-m2.5": "opencode/minimax-m2.5-free",
    "minimax-m2.5-free": "opencode/minimax-m2.5-free",
    "kimi": "cloudflare-workers-ai/@cf/moonshotai/kimi-k2.6",
    "kimi-2.6": "cloudflare-workers-ai/@cf/moonshotai/kimi-k2.6",
    "kimi-k2.6": "cloudflare-workers-ai/@cf/moonshotai/kimi-k2.6",
    "cloudflare": "cloudflare-workers-ai/@cf/moonshotai/kimi-k2.6",
}

TRIAGE_ONLY_MODE = True
MAX_CRAWL_DEPTH = 2
MAX_STORED_SEARCH_QUERIES = 15
MAX_STORED_SOURCES = 20
MAX_STORED_DIAGNOSTICS = 10

VALID_TYPES = {
    "hackathon",
    "ai_challenge",
    "cybersecurity_competition",
    "coding_competition",
    "defence_innovation_challenge",
    "technical_challenge",
    "ctf",
}

ALLOWED_EVENT_TYPES = VALID_TYPES

HIGH_PRIORITY_DOMAINS = [
    "idex.gov.in",
    "drdo.gov.in",
    "isro.gov.in",
    "indiaai.gov.in",
    "aikosh.indiaai.gov.in",
    "sih.gov.in",
    "mygov.in",
    "innovateindia.mygov.in",
    "nic.in",
    "meity.gov.in",
]

LOW_PRIORITY_DOMAINS = [
    "startupindia.gov.in",
    "startupgrantsindia.com",
    "incubator portals",
    "grant portals",
]

BLOCK_TERMS = [
    "call for proposals",
    "cfp",
    "request for proposal",
    "rfp",
    "grant",
    "funding",
    "seed fund",
    "venture",
    "accelerator",
    "incubation",
    "incubator",
    "cohort",
    "fellowship",
    "scholarship",
    "startup recognition",
    "procurement",
    "expression of interest",
    "eoi",
    "tender",
    "vendor empanelment",
    "investment",
    "equity",
    "research funding",
    "commercialization support",
    "startup support",
    "startup ecosystem",
    "innovation ecosystem",
    "partner ecosystem",
    "proposal submission",
    "research proposal",
    "grants portal",
    "funding support",
    "financial assistance",
]

POSITIVE_SIGNALS = [
    "hackathon",
    "challenge",
    "competition",
    "capture the flag",
    "ctf",
    "problem statement",
    "submit solution",
    "team size",
    "cash prize",
    "leaderboard",
    "winner",
    "runner up",
    "grand finale",
    "evaluation criteria",
    "judging criteria",
    "prototype submission",
    "coding_competition",
    "coding challenge",
    "cyber challenge",
    "ai challenge",
    "innovation challenge",
    "technical challenge",
]

STRICT_SEARCH_QUERIES = [
    "site:gov.in hackathon registration open",
    "site:gov.in ai challenge apply now",
    "site:gov.in cybersecurity competition registration",
    "site:gov.in defence challenge apply",
    "site:gov.in coding competition registration",
    "site:gov.in ctf registration",
    "site:gov.in technical challenge problem statement",
    "site:idex.gov.in challenge open",
    "site:drdo.gov.in challenge registration",
    "site:isro.gov.in hackathon registration",
    "site:indiaai.gov.in ai challenge",
    "site:aikosh.indiaai.gov.in competitions",
    "site:sih.gov.in registration",
    "site:mygov.in hackathon",
    "site:nic.in cyber challenge",
]

ACTIVE_STATUSES = {
    "registration_open",
    "submission_open",
    "application_open",
}

OPEN_KEYWORDS = [
    "apply now",
    "register now",
    "registration open",
    "registrations open",
    "submit now",
    "submission open",
    "submissions open",
    "applications open",
    "accepting applications",
    "challenge open",
    "participate now",
    "last date to apply",
    "last date for submission",
]

CLOSED_KEYWORDS = [
    "registration closed",
    "registrations closed",
    "submission closed",
    "submissions closed",
    "applications closed",
    "deadline over",
    "deadline has passed",
    "winners announced",
    "winner announced",
    "results announced",
    "finalists announced",
    "closed for submissions",
    "no longer accepting",
    "event completed",
]

HARD_CLOSED_KEYWORDS = [
    "registration closed",
    "registrations closed",
    "submission closed",
    "submissions closed",
    "applications closed",
    "closed for submissions",
    "no longer accepting",
]

AUTH_KEYWORDS = [
    "sign in",
    "login",
    "create account",
    "oauth",
    "single sign-on",
]

FORM_KEYWORDS = [
    "<form",
    "type=\"submit\"",
    "type='submit'",
    "application form",
    "registration form",
    "submit application",
    "start application",
]

GRANT_ONLY_KEYWORDS = [
    "call for proposals",
    "request for proposals",
    "rolling call",
    "grant call",
    "funding call",
    "joint innovation call",
    "innovation call",
    "r&d collaboration",
    "research and development collaboration",
    "project funding",
    "expression of interest",
]

R_AND_D_PROPOSAL_ONLY_KEYWORDS = [
    "call for proposal",
    "call for proposals",
    "callforproposals",
    "request for proposal",
    "request for proposals",
    "research proposal",
    "research proposals",
    "joint research",
    "collaborative research",
    "r&d",
    "r & d",
    "research and development",
    "rdi fund",
    "rdif",
    "cfp",
    "project funding",
    "funding call",
    "grant call",
    "howtosubmitproposal",
    "s&t cooperation call",
]

COMPETITION_KEYWORDS = [
    "hackathon",
    "challenge",
    "competition",
    "contest",
    "grand challenge",
    "innovation challenge",
]

STARTUP_FUND_KEYWORDS = [
    "startup",
    "seed",
    "seed funding",
    "seed-stage",
    "pre-seed",
    "incubator",
    "incubation",
    "accelerator",
    "accelerator program",
    "funding",
    "grant",
    "venture",
    "vc ",
    "angel",
    "investor",
    "pitch",
    "pitch day",
    "demo day",
]

ABSOLUTE_NON_TECHNICAL_KEYWORDS = [
    "logo contest",
    "logo",
    "mascot contest",
    "mascot",
    "slogan contest",
    "slogan",
    "photography contest",
    "photography",
    "poster contest",
    "poster",
    "essay contest",
    "essay",
    "poetry contest",
    "poetry",
    "debate contest",
    "debate",
    "drawing contest",
    "drawing",
    "art contest",
    "sketch",
    "painting",
    "reel contest",
    "social media",
    "social media campaign",
    "awareness campaign",
    "branding competition",
    "public voting contest",
    "quiz competition",
    "cultural",
    "cultural competition",
    "talent competition",
    "creative writing",
]

STRONG_TECHNICAL_SIGNALS = [
    "ai",
    "artificial intelligence",
    "ml",
    "machine learning",
    "cybersecurity",
    "cyber security",
    "coding",
    "software",
    "hardware",
    "robotics",
    "engineering",
    "prototype",
    "automation",
    "aerospace",
    "defence tech",
    "defense tech",
    "defence technology",
    "defense technology",
    "govtech",
    "api",
    "cloud",
    "blockchain",
    "semiconductor",
    "embedded systems",
    "biotechnology r&d",
    "biotechnology",
    "bio-manufacturing",
    "biotechnology research",
    "technical research",
    "technical r&d",
    "proof of concept",
    "poc",
    "model development",
    "data science",
    "nlp",
    "regtech",
    "biomanufacturing",
    "bio-ai",
    "bio ai",
    "aircraft",
    "systems",
]

IMPLEMENTATION_SIGNALS = [
    "prototype",
    "proof of concept",
    "poc",
    "software",
    "ai model",
    "model",
    "code",
    "hardware design",
    "engineering solution",
    "technical architecture",
    "research implementation",
    "automation system",
    "technical solution",
    "algorithm",
    "workflow automation",
    "data anonymisation",
    "data anonymization",
    "biomanufacturing",
    "development and production",
]

CONTEXTUAL_IMPLEMENTATION_TERMS = [
    "propose",
    "project",
    "solution",
    "solutions",
    "application",
    "platform",
    "system",
]

NON_IMPLEMENTATION_ONLY_KEYWORDS = [
    "ideas only",
    "idea only",
    "opinion",
    "opinions",
    "awareness content",
    "branding",
    "writing",
    "visual design",
    "artwork",
    "creative submission",
]

SCHOOL_AWARENESS_ONLY_KEYWORDS = [
    "school student",
    "school students",
    "category 1",
    "category-1",
    "awareness",
    "awareness content",
    "video submission",
    "essay",
    "poster",
]

PROCUREMENT_OR_GRANT_INTAKE_KEYWORDS = [
    "procurement",
    "procurement notice",
    "rfp",
    "request for proposal",
    "request for proposals",
    "call for proposal",
    "call for proposals",
    "grant intake",
    "grant application",
    "grant funding",
    "funding support",
    "funding application",
    "vendor onboarding",
    "vendor registration",
    "empanelment",
    "tender",
    "bid",
    "bidding",
    "acquisition",
    "defence acquisition",
    "feasibility study",
    "feasibility project",
    "expression of interest",
]

FORBIDDEN_OPPORTUNITY_KEYWORDS = [
    "accelerator",
    "accelerator cohort",
    "accelerator program",
    "acquisition",
    "bid",
    "bidding",
    "call for proposal",
    "call for proposals",
    "cfp",
    "empanelment",
    "expression of interest",
    "fellowship",
    "funding call",
    "grant",
    "grant call",
    "grant funding",
    "incubation",
    "incubator",
    "investment program",
    "joint innovation call",
    "procurement",
    "request for proposal",
    "request for proposals",
    "research proposal",
    "r&d solicitation",
    "seed funding",
    "startup funding",
    "tender",
    "vendor onboarding",
    "vendor registration",
]

HACKATHON_TYPE_KEYWORDS = [
    "hackathon",
    "datathon",
    "codeathon",
]

CYBERSECURITY_TYPE_KEYWORDS = [
    "cybersecurity",
    "cyber security",
    "ctf",
    "capture the flag",
    "bug bounty",
    "security challenge",
]

DEFENCE_TYPE_KEYWORDS = [
    "defence",
    "defense",
    "idex",
    "drdo",
    "armed forces",
    "military",
    "aerospace",
    "space defence",
]

AI_TYPE_KEYWORDS = [
    "ai",
    "artificial intelligence",
    "machine learning",
    "ml",
    "deep learning",
    "data science",
    "nlp",
    "computer vision",
    "model development",
]

CODING_TYPE_KEYWORDS = [
    "coding",
    "programming",
    "software development",
    "algorithm",
    "app development",
    "developer challenge",
]

HARD_PROCUREMENT_KEYWORDS = [
    "procurement notice",
    "vendor onboarding",
    "vendor registration",
    "empanelment",
    "tender",
    "bidding",
    "defence acquisition",
    "feasibility study",
    "feasibility project",
]

COMPETITIVE_STRUCTURE_SIGNALS = [
    "hackathon",
    "challenge",
    "open challenge",
    "innovation challenge",
    "competition",
    "problem statement",
    "submit solution",
    "team size",
    "cash prize",
    "leaderboard",
    "shortlist",
    "selected",
    "winner",
    "winners",
    "prize",
    "prizes",
    "demo",
    "jury",
    "evaluation",
    "judging",
    "grand finale",
    "prototype submission",
    "capture the flag",
    "ctf",
]

TECHNICAL_EVALUATION_SIGNALS = [
    "technical evaluation",
    "technical judging",
    "evaluation criteria",
    "judging criteria",
    "prototype evaluation",
    "proof of concept",
    "demo",
    "pitch",
    "jury",
    "selection committee",
    "technical committee",
]

TITLE_STOP_WORDS = {
    "a",
    "an",
    "and",
    "for",
    "of",
    "on",
    "the",
    "to",
    "in",
    "by",
    "with",
    "under",
    "call",
    "calls",
    "proposal",
    "proposals",
    "challenge",
    "competition",
    "contest",
    "hackathon",
    "joint",
    "open",
    "grand",
}

TRUSTED_GOVERNMENT_HOSTS = {
    "aicte-india.org",
    "aim.gov.in",
    "aikosh.indiaai.gov.in",
    "birac.nic.in",
    "bhashini.gov.in",
    "data.gov.in",
    "dbtindia.gov.in",
    "digitalindia.gov.in",
    "drdo.gov.in",
    "dst.gov.in",
    "event.data.gov.in",
    "idex.gov.in",
    "indiaai.gov.in",
    "innovateindia.mygov.in",
    "isro.gov.in",
    "meity.gov.in",
    "mygov.in",
    "nha.gov.in",
    "npci.org.in",
    "pib.gov.in",
    "sih.gov.in",
    "startupindia.gov.in",
    "tdb.gov.in",
}

TRUSTED_SUFFIXES = (
    ".gov.in",
    ".nic.in",
    ".ac.in",
    ".edu.in",
)

EXTERNAL_REGISTRATION_HOSTS = {
    "airmeet.com",
    "devfolio.co",
    "devpost.com",
    "docs.google.com",
    "form.startuptn.in",
    "forms.gle",
    "hack2skill.com",
    "hackerearth.com",
    "masaforum.com",
    "typeform.com",
    "unstop.com",
    "www.hackerearth.com",
    "www.typeform.com",
}

RECALL_TIER_ORDER = {
    "rejected": 0,
    "archived": 1,
    "borderline": 2,
    "likely_active": 3,
    "fully_verified": 4,
}

SOFT_EXCLUDED_REASONS = {
    "borderline",
    "unverifiable",
    "partial_verification",
    "no_new_registration",
    "workflow_unclear",
    "auth_wall",
    "external_registration",
}

ARCHIVED_REASON_KEYWORDS = (
    "closed",
    "stale",
    "archived",
    "expired",
    "deadline passed",
    "winner",
    "finalist",
    "finale",
    "completed",
    "historical",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover and validate active Indian government opportunities."
    )
    parser.add_argument(
        "-m",
        "--model",
        help="Run one model only. Aliases: big-pickle, hy3, kimi, cloudflare.",
    )
    parser.add_argument(
        "--models",
        help="Comma-separated fallback model list. Aliases are accepted.",
    )
    parser.add_argument(
        "--current-date",
        default=os.environ.get("CURRENT_DATE", date.today().isoformat()),
        help="Validation date in YYYY-MM-DD format. Defaults to today.",
    )
    parser.add_argument(
        "--output",
        default=str(OUTPUT_JSON),
        help="Final JSON output path.",
    )
    parser.add_argument(
        "--validate-existing",
        metavar="PATH",
        help="Normalize and validate an existing JSON file without calling OpenCode.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the discovery plan without running OpenCode.",
    )
    parser.add_argument(
        "--skip-live-validation",
        action="store_true",
        help="Skip HTTP page checks and rely on deterministic field validation.",
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Ignore cached successful task outputs.",
    )
    parser.add_argument(
        "--max-tasks",
        type=int,
        default=None,
        help="Limit discovery tasks for smoke tests or targeted runs.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Deprecated: ignored. Timeouts are model-specific via MODEL_TIMEOUTS.",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=1,
        help="Attempts per model per discovery task. Default is 1 to avoid long stalls.",
    )
    parser.add_argument(
        "--retry-timeouts",
        action="store_true",
        help="Retry a model after a timeout. By default timeout immediately falls back.",
    )
    parser.add_argument(
        "--run-budget",
        type=int,
        default=0,
        help=(
            "Deprecated: ignored. Global run budgets are disabled so fallback models can finish."
        ),
    )
    parser.add_argument(
        "--allow-empty-output",
        action="store_true",
        help="Allow a failed/empty discovery run to overwrite an existing output file.",
    )
    parser.add_argument(
        "--silent",
        action="store_true",
        help="Disable all TUI output, including the dashboard and typing indicators.",
    )
    parser.add_argument(
        "--max-candidates-per-task",
        type=int,
        default=20,
        help="Candidate cap requested from each bounded discovery task.",
    )
    parser.add_argument(
        "--min-confidence",
        type=int,
        default=72,
        help="Minimum calibrated confidence for active inclusion.",
    )
    parser.add_argument(
        "--stop-on-saturation",
        action="store_true",
        help="Stop after repeated low-novelty discovery tasks.",
    )
    return parser.parse_args()


def resolve_model(model: str | None) -> str | None:
    if not model:
        return None
    return MODEL_ALIASES.get(model.strip().lower(), model.strip())


def resolve_models(args: argparse.Namespace) -> list[str]:
    if args.model and args.models:
        raise SystemExit("Use either --model or --models, not both.")
    if args.model:
        return [resolve_model(args.model) or args.model]
    if args.models:
        models = [resolve_model(part) for part in args.models.split(",")]
        return [model for model in models if model]
    return DEFAULT_MODELS


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    temp_path.replace(path)


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        handle.write(content)
    temp_path.replace(path)


class TypingIndicator:
    def __init__(self, state: str, message: str, enabled: bool | None = None, interval: float = 0.12) -> None:
        self.state = state
        self.message = message
        self.enabled = sys.stdout.isatty() if enabled is None else enabled
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._frames = itertools.cycle(["·", "··", "···", "····", "···", "··", "·"])

    def __enter__(self) -> "TypingIndicator":
        if self.enabled:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        else:
            print(f"{self.state}... {self.message}")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.enabled:
            self._stop.set()
            if self._thread:
                self._thread.join(timeout=1)
            sys.stdout.write("\r\x1b[K")
            sys.stdout.flush()
        print(f"completed... {self.message}")

    def update(self, message: str) -> None:
        with self._lock:
            self.message = message

    def _snapshot(self) -> tuple[str, str]:
        with self._lock:
            return self.state, self.message

    def _run(self) -> None:
        while not self._stop.is_set():
            state, message = self._snapshot()
            frame = next(self._frames)
            sys.stdout.write(f"\r\x1b[K{state}... {message} {frame}")
            sys.stdout.flush()
            if self._stop.wait(self.interval):
                break


class NullContext(AbstractContextManager):
    def __enter__(self) -> "NullContext":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class DashboardStage(AbstractContextManager):
    def __init__(self, dashboard: "TerminalDashboard", state: str, message: str) -> None:
        self.dashboard = dashboard
        self.state = state
        self.message = message

    def __enter__(self) -> "DashboardStage":
        self.dashboard.set_phase(self.state, self.message)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self.dashboard.set_phase("completed", self.message)
        return False


class TerminalDashboard:
    def __init__(self, enabled: bool = False, refresh_interval: float = 0.12) -> None:
        self.enabled = enabled
        self.refresh_interval = refresh_interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._spinner = itertools.cycle(["|", "/", "-", "\\"])
        self._started_at = time.monotonic()
        self._task_started_at = self._started_at
        self._title = "Hackathon Intelligence TUI"
        self._phase = "thinking"
        self._phase_message = "starting"
        self._task_name = "idle"
        self._task_detail = "waiting"
        self._model_name = "n/a"
        self._model_detail = "n/a"
        self._counters = {
            "tasks_total": 0,
            "tasks_done": 0,
            "tasks_failed": 0,
            "accepted": 0,
            "novelty": 0,
            "candidates": 0,
            "rejected": 0,
        }
        self._summary = {
            "current_date": "n/a",
            "models": "n/a",
            "live_validation": "n/a",
            "coverage_confidence": "n/a",
            "coverage_status": "n/a",
        }
        self._last_note = ""

    def __enter__(self) -> "TerminalDashboard":
        if self.enabled:
            self._started_at = time.monotonic()
            self._task_started_at = self._started_at
            sys.stdout.write("\x1b[?25l\x1b[2J\x1b[H")
            sys.stdout.flush()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self.enabled:
            self._stop.set()
            if self._thread:
                self._thread.join(timeout=1)
            self.render(final=True)
            sys.stdout.write("\x1b[?25h\n")
            sys.stdout.flush()
        return False

    def stage(self, state: str, message: str) -> AbstractContextManager:
        if not self.enabled:
            return NullContext()
        return DashboardStage(self, state, message)

    def set_phase(self, state: str, message: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._phase = state
            self._phase_message = message

    def set_summary(self, **fields: Any) -> None:
        if not self.enabled:
            return
        with self._lock:
            for key, value in fields.items():
                if key in self._summary:
                    self._summary[key] = value

    def set_task(self, name: str, detail: str | None = None) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._task_name = name
            self._task_started_at = time.monotonic()
            if detail is not None:
                self._task_detail = detail

    def set_model(self, name: str, detail: str | None = None) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._model_name = name
            if detail is not None:
                self._model_detail = detail

    def bump(self, **fields: int) -> None:
        if not self.enabled:
            return
        with self._lock:
            for key, value in fields.items():
                if key in self._counters:
                    self._counters[key] = value

    def note(self, message: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._last_note = message

    def render(self, final: bool = False) -> None:
        if not self.enabled:
            return
        with self._lock:
            spinner = next(self._spinner)
            elapsed_seconds = max(0.0, time.monotonic() - self._started_at)
            eta_seconds = self._estimate_eta()
            progress = self._progress_fraction()
            lines = [
                "+------------------------------------------------------------+",
                f"| {self._title:<58} |",
                "+------------------------------------------------------------+",
                f"| {spinner} {self._phase:<12} {self._phase_message:<41} |",
                "+-------------------------+-------------------------------+",
                f"| Current Task            | {self._task_name:<29} |",
                f"| Task Detail             | {self._task_detail:<29} |",
                "+-------------------------+-------------------------------+",
                f"| Model Status            | {self._model_name:<29} |",
                f"| Model Detail            | {self._model_detail:<29} |",
                "+-------------------------+-------------------------------+",
                f"| Tasks Total             | {self._counters['tasks_total']:<29} |",
                f"| Tasks Done              | {self._counters['tasks_done']:<29} |",
                f"| Tasks Failed            | {self._counters['tasks_failed']:<29} |",
                f"| Accepted                | {self._counters['accepted']:<29} |",
                f"| Novelty                 | {self._counters['novelty']:<29} |",
                f"| Candidates              | {self._counters['candidates']:<29} |",
                f"| Rejected                | {self._counters['rejected']:<29} |",
                "+-------------------------+-------------------------------+",
                f"| Current Date            | {self._summary['current_date']:<29} |",
                f"| Models                  | {self._summary['models']:<29} |",
                f"| Live Validation         | {self._summary['live_validation']:<29} |",
                f"| Coverage Confidence     | {self._summary['coverage_confidence']:<29} |",
                f"| Coverage Status         | {self._summary['coverage_status']:<29} |",
                "+-------------------------+-------------------------------+",
                f"| Note                    | {self._last_note:<29} |",
                "+------------------------------------------------------------+",
                self._status_footer(elapsed_seconds, eta_seconds, progress),
            ]
            sys.stdout.write("\x1b[H" + "\n".join(lines))
            if final:
                sys.stdout.write("\n")
            sys.stdout.flush()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.render(final=False)
            if self._stop.wait(self.refresh_interval):
                break

    def _progress_fraction(self) -> float:
        total = max(0, self._counters.get("tasks_total", 0))
        done = min(max(0, self._counters.get("tasks_done", 0)), total)
        if total <= 0:
            return 0.0
        return done / total

    def _estimate_eta(self) -> float | None:
        total = max(0, self._counters.get("tasks_total", 0))
        done = min(max(0, self._counters.get("tasks_done", 0)), total)
        if total <= 0 or done <= 0:
            return None
        elapsed = max(0.0, time.monotonic() - self._started_at)
        avg_per_task = elapsed / done
        remaining = max(0, total - done)
        return max(0.0, avg_per_task * remaining)

    def _format_duration(self, seconds: float | None) -> str:
        if seconds is None:
            return "--:--"
        seconds = max(0.0, seconds)
        minutes, remainder = divmod(int(seconds), 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{remainder:02d}"
        return f"{minutes:02d}:{remainder:02d}"

    def _status_footer(self, elapsed_seconds: float, eta_seconds: float | None, progress: float) -> str:
        bar_width = 12
        filled = min(bar_width, max(0, int(progress * bar_width)))
        bar = "█" * filled + "░" * (bar_width - filled)
        eta_text = self._format_duration(eta_seconds)
        elapsed_text = self._format_duration(elapsed_seconds)
        percent_text = f"{progress * 100:5.1f}%"
        footer = f"{percent_text} [{bar}] el {elapsed_text} eta {eta_text}"
        return f"| {footer[:58]:<58} |"


def read_json_file(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize_for_gate(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).casefold()
    text = text.replace("&", " and ")
    text = re.sub(r"[\W_]+", " ", text, flags=re.UNICODE)
    return normalize_space(text)


class HardNegativeGate:
    def __init__(self, block_terms: list[str] | None = None) -> None:
        self.block_terms = block_terms or BLOCK_TERMS
        self._normalized_terms = [normalize_for_gate(term) for term in self.block_terms]

    def blocked_terms(self, value: Any) -> list[str]:
        text = normalize_for_gate(value)
        if not text:
            return []
        return [
            original
            for original, normalized in zip(self.block_terms, self._normalized_terms, strict=False)
            if normalized and re.search(rf"(?<!\w){re.escape(normalized)}(?!\w)", text, flags=re.UNICODE)
        ]

    def should_reject(self, value: Any) -> bool:
        return bool(self.blocked_terms(value))


class CompetitionSignalEngine:
    def __init__(self, signals: list[str] | None = None, threshold: float = 0.45) -> None:
        self.signals = signals or POSITIVE_SIGNALS
        self.threshold = threshold
        self._normalized_signals = [normalize_for_gate(signal) for signal in self.signals]

    def score(self, value: Any) -> tuple[float, list[str]]:
        text = normalize_for_gate(value)
        if not text:
            return 0.0, []
        matched = [
            original
            for original, normalized in zip(self.signals, self._normalized_signals, strict=False)
            if normalized and re.search(rf"(?<!\w){re.escape(normalized)}(?!\w)", text, flags=re.UNICODE)
        ]
        return round(min(1.0, len(matched) * 0.15), 4), matched

    def passes(self, value: Any) -> bool:
        score, _ = self.score(value)
        return score >= self.threshold


def strip_tags(markup: str) -> str:
    markup = re.sub(r"(?is)<script.*?</script>", " ", markup)
    markup = re.sub(r"(?is)<style.*?</style>", " ", markup)
    markup = re.sub(r"(?s)<[^>]+>", " ", markup)
    return normalize_space(html.unescape(markup))


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "opportunity"


def parse_jsonish(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return None
    if stripped[0] in "[{":
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return value
    return value


def ensure_list(value: Any) -> list[Any]:
    value = parse_jsonish(value)
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple | set):
        return list(value)
    if isinstance(value, str):
        if not value.strip():
            return []
        if "," in value or ";" in value:
            return [part.strip() for part in re.split(r"[,;]", value) if part.strip()]
        return [value.strip()]
    return [value]


def ensure_dict(value: Any) -> dict[str, Any]:
    value = parse_jsonish(value)
    if isinstance(value, dict):
        return value
    if value is None or value == "":
        return {}
    return {"summary": value}


def ensure_bool(value: Any) -> bool | None:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1", "open", "available"}:
            return True
        if lowered in {"false", "no", "0", "closed", "unavailable"}:
            return False
    return None


def ensure_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        match = re.search(r"-?\d+", value)
        if match:
            return int(match.group(0))
    return default


def canonicalize_url(raw_url: Any) -> str | None:
    if raw_url is None:
        return None
    url = str(raw_url).strip()
    if not url or url.lower() in {"null", "none", "n/a", "na"}:
        return None
    if not re.match(r"^https?://", url, flags=re.I):
        url = "https://" + url.lstrip("/")
    parsed = urlparse(url)
    if not parsed.netloc:
        return None
    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower()
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in {"fbclid", "gclid", "mc_cid", "mc_eid"}
    ]
    query = urlencode(sorted(query_pairs), doseq=True)
    return urlunparse((scheme, netloc, path, "", query, ""))


def hostname(url: str | None) -> str:
    if not url:
        return ""
    return urlparse(url).netloc.lower().removeprefix("www.")


def is_trusted_government_url(url: str | None) -> bool:
    host = hostname(url)
    if not host:
        return False
    if host in TRUSTED_GOVERNMENT_HOSTS:
        return True
    return any(host.endswith(suffix) for suffix in TRUSTED_SUFFIXES)


def is_external_registration_url(url: str | None) -> bool:
    host = hostname(url)
    return host in EXTERNAL_REGISTRATION_HOSTS or any(
        host.endswith("." + trusted) for trusted in EXTERNAL_REGISTRATION_HOSTS
    )


DATE_PATTERNS = [
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%d.%m.%Y",
    "%d %B %Y",
    "%d %b %Y",
    "%B %d, %Y",
    "%b %d, %Y",
]


def parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if not text or text.lower() in {"null", "none", "n/a", "na"}:
        return None

    iso_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    if iso_match:
        try:
            return datetime.strptime(iso_match.group(1), "%Y-%m-%d").date()
        except ValueError:
            pass

    for pattern in DATE_PATTERNS:
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def iso_date(value: Any) -> str | None:
    parsed = parse_date(value)
    return parsed.isoformat() if parsed else None


class SourceValidation(BaseModel):
    model_config = ConfigDict(extra="allow")

    source_url: str | None = None
    source_type: str | None = None
    registration_page_verified: bool = False
    registration_page_status_code: int | None = None
    deadline_verified: bool = False
    official_confirmation_found: bool = False
    official_open_keywords_found: list[str] = Field(default_factory=list)
    closed_keywords_found: list[str] = Field(default_factory=list)
    form_detected: bool = False
    registration_form_detected: bool = False
    auth_wall_detected: bool = False
    zombie_page_suspected: bool = False
    final_url: str | None = None
    validation_errors: list[str] = Field(default_factory=list)

    @field_validator("official_open_keywords_found", "closed_keywords_found", "validation_errors", mode="before")
    @classmethod
    def _lists(cls, value: Any) -> list[Any]:
        return ensure_list(value)


class OpportunityRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    event_type: str = "technical_challenge"
    hackathon_name: str = ""
    full_name: str | None = None
    slug: str | None = None
    current_status: str = "unknown"
    is_open_for_new_registration: bool | None = None
    is_open_for_submission: bool | None = None
    verification_state: str = "needs_review"
    registration_url: str | None = None
    submission_url: str | None = None
    official_website: str | None = None
    official_event_page: str | None = None
    application_portal: str | None = None
    hosting_organization: str | None = None
    co_organizers: list[Any] = Field(default_factory=list)
    ministry: str | None = None
    department: str | None = None
    institution_type: str | None = None
    platform: str | None = None
    domain: str | None = None
    subdomains: list[Any] = Field(default_factory=list)
    theme: str | None = None
    focus_areas: list[Any] = Field(default_factory=list)
    problem_statements: list[Any] = Field(default_factory=list)
    sdg_alignment: list[Any] = Field(default_factory=list)
    deadline: str | None = None
    registration_open_date: str | None = None
    registration_close_date: str | None = None
    submission_open_date: str | None = None
    submission_close_date: str | None = None
    application_deadline: str | None = None
    proposal_deadline: str | None = None
    event_start_date: str | None = None
    event_end_date: str | None = None
    timezone: str = "Asia/Kolkata"
    eligibility_criteria: dict[str, Any] = Field(default_factory=dict)
    team_size: dict[str, Any] = Field(default_factory=dict)
    submission_fee: dict[str, Any] = Field(default_factory=dict)
    prizes: dict[str, Any] = Field(default_factory=dict)
    funding_support: dict[str, Any] = Field(default_factory=dict)
    incubation_support: dict[str, Any] = Field(default_factory=dict)
    mentorship_support: dict[str, Any] = Field(default_factory=dict)
    internship_opportunities: dict[str, Any] = Field(default_factory=dict)
    procurement_or_pilot_opportunity: dict[str, Any] = Field(default_factory=dict)
    ipr_policy: dict[str, Any] = Field(default_factory=dict)
    format: str | None = None
    mode: str | None = None
    participation_mode: list[Any] = Field(default_factory=list)
    location: dict[str, Any] = Field(default_factory=dict)
    communication_channels: dict[str, Any] = Field(default_factory=dict)
    resources: dict[str, Any] = Field(default_factory=dict)
    source_url: str | None = None
    source_type: str | None = None
    source_validation: SourceValidation = Field(default_factory=SourceValidation)
    confidence_score: int = 0
    confidence_reasons: list[str] = Field(default_factory=list)
    search_metadata: dict[str, Any] = Field(default_factory=dict)
    deduplication: dict[str, Any] = Field(default_factory=dict)
    discovery_provenance: dict[str, Any] = Field(default_factory=dict)
    classification_tier: str = "borderline"
    tier_reason: str | None = None
    admin_review_recommended: bool = False
    human_verification_needed: bool = False
    blue_tick_eligible: bool = False
    tags: list[Any] = Field(default_factory=list)
    date_searched: str | None = None
    exclusion_reason: str | None = None
    borderline_reason: str | None = None

    @field_validator(
        "co_organizers",
        "subdomains",
        "focus_areas",
        "problem_statements",
        "sdg_alignment",
        "participation_mode",
        "tags",
        "confidence_reasons",
        mode="before",
    )
    @classmethod
    def _normalize_lists(cls, value: Any) -> list[Any]:
        return ensure_list(value)

    @field_validator(
        "eligibility_criteria",
        "team_size",
        "submission_fee",
        "prizes",
        "funding_support",
        "incubation_support",
        "mentorship_support",
        "internship_opportunities",
        "procurement_or_pilot_opportunity",
        "ipr_policy",
        "location",
        "communication_channels",
        "resources",
        "search_metadata",
        "deduplication",
        "discovery_provenance",
        mode="before",
    )
    @classmethod
    def _normalize_dicts(cls, value: Any) -> dict[str, Any]:
        return ensure_dict(value)

    @field_validator("confidence_score", mode="before")
    @classmethod
    def _normalize_confidence(cls, value: Any) -> int:
        return max(0, min(99, ensure_int(value, 0)))

    @field_validator("is_open_for_new_registration", "is_open_for_submission", mode="before")
    @classmethod
    def _normalize_bools(cls, value: Any) -> bool | None:
        return ensure_bool(value)

    @field_validator(
        "registration_url",
        "submission_url",
        "official_website",
        "official_event_page",
        "source_url",
        mode="before",
    )
    @classmethod
    def _normalize_urls(cls, value: Any) -> str | None:
        return canonicalize_url(value)

    @field_validator(
        "deadline",
        "registration_open_date",
        "registration_close_date",
        "submission_open_date",
        "submission_close_date",
        "application_deadline",
        "proposal_deadline",
        "event_start_date",
        "event_end_date",
        "date_searched",
        mode="before",
    )
    @classmethod
    def _normalize_dates(cls, value: Any) -> str | None:
        return iso_date(value)

    @field_validator("event_type", mode="before")
    @classmethod
    def _normalize_event_type(cls, value: Any) -> str:
        text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "defense_challenge": "defence_innovation_challenge",
            "defence_challenge": "defence_innovation_challenge",
            "ai_competition": "ai_challenge",
            "cyber_challenge": "cybersecurity_competition",
            "cyber_competition": "cybersecurity_competition",
            "coding_challenge": "coding_competition",
            "innovation_challenge": "technical_challenge",
            "innovation_competition": "technical_challenge",
            "technical_competition": "technical_challenge",
            "capture_the_flag": "ctf",
        }
        text = aliases.get(text, text)
        return text if text in ALLOWED_EVENT_TYPES else "technical_challenge"

    @field_validator("current_status", mode="before")
    @classmethod
    def _normalize_status(cls, value: Any) -> str:
        text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "open": "registration_open",
            "live": "registration_open",
            "active": "registration_open",
            "accepting_applications": "application_open",
            "accepting_submissions": "submission_open",
            "closed": "closed",
        }
        return aliases.get(text, text or "unknown")

    @model_validator(mode="after")
    def _fill_defaults(self) -> "OpportunityRecord":
        self.hackathon_name = normalize_space(self.hackathon_name or self.full_name or "Untitled Opportunity")
        self.full_name = normalize_space(self.full_name or self.hackathon_name)
        self.slug = self.slug or slugify(self.full_name)
        self.id = self.id or self.slug
        self.source_url = self.source_url or self.official_event_page or self.registration_url or self.official_website
        self.official_event_page = self.official_event_page or self.source_url
        if not self.deadline:
            self.deadline = (
                self.application_deadline
                or self.registration_close_date
                or self.submission_close_date
                or self.proposal_deadline
            )
        self.deduplication.setdefault("canonical_url", self.source_url or self.registration_url)
        self.deduplication.setdefault("normalized_title", slugify(self.hackathon_name))
        return self


@dataclass(frozen=True)
class DiscoveryTask:
    name: str
    category: str
    priority: int
    seed_urls: list[str]
    queries: list[str]

    @property
    def key(self) -> str:
        return stable_hash(json.dumps(self.__dict__, sort_keys=True))


@dataclass
class RunArtifact:
    task_name: str
    model: str
    attempt: int
    success: bool
    output: str
    error: str | None
    elapsed_seconds: float
    from_cache: bool = False
    timed_out: bool = False
    timeout_seconds: int | None = None
    partial_output_path: str | None = None
    raw_task_output_path: str | None = None
    parser_accepted: bool | None = None
    parser_diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass
class PageProbe:
    url: str
    final_url: str | None = None
    status_code: int | None = None
    ok: bool = False
    elapsed_seconds: float = 0.0
    text_excerpt: str = ""
    raw_excerpt: str = ""
    open_keywords: list[str] = field(default_factory=list)
    closed_keywords: list[str] = field(default_factory=list)
    form_detected: bool = False
    auth_wall_detected: bool = False
    zombie_page_suspected: bool = False
    error: str | None = None


class PageFingerprintCache:
    def __init__(self, cache_dir: Path = CACHE_DIR / "page_fingerprints") -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, url: str) -> Path:
        return self.cache_dir / f"{stable_hash(url)}.json"

    def ttl_hours(self, url: str) -> int:
        host = hostname(url)
        if host in HIGH_PRIORITY_DOMAINS or any(host.endswith("." + domain) for domain in HIGH_PRIORITY_DOMAINS):
            return 6
        return 24

    def load_if_fresh(self, url: str) -> PageProbe | None:
        path = self._path(url)
        if not path.exists():
            return None
        try:
            payload = read_json_file(path)
            saved_at = datetime.fromisoformat(str(payload.get("saved_at")))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None
        age_hours = (datetime.now(UTC) - saved_at).total_seconds() / 3600
        if age_hours > self.ttl_hours(url):
            return None
        probe_payload = payload.get("probe")
        if not isinstance(probe_payload, dict):
            return None
        return PageProbe(**probe_payload)

    def save(self, url: str, probe: PageProbe, raw: str = "", headers: dict[str, str] | None = None) -> None:
        normalized_content = normalize_for_gate(probe.text_excerpt)
        payload = {
            "url": url,
            "saved_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "html_hash": hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest() if raw else None,
            "last_modified": (headers or {}).get("Last-Modified"),
            "etag": (headers or {}).get("ETag"),
            "normalized_content_fingerprint": hashlib.sha256(normalized_content.encode("utf-8")).hexdigest(),
            "probe": probe.__dict__,
        }
        atomic_write_json(self._path(url), payload)


@dataclass
class CandidateDecision:
    bucket: str
    record: OpportunityRecord
    reason: str


class CheckpointStore:
    def __init__(self, cache_dir: Path = CACHE_DIR) -> None:
        self.cache_dir = cache_dir
        self.run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        self.run_dir = cache_dir / "runs" / self.run_id
        self.task_cache_dir = cache_dir / "tasks"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.task_cache_dir.mkdir(parents=True, exist_ok=True)

    def load_success(self, task: DiscoveryTask, current_date: str, refresh: bool) -> str | None:
        payload = self.load_success_payload(task, current_date, refresh)
        if not payload:
            return None
        return payload.get("output")

    def load_success_payload(
        self,
        task: DiscoveryTask,
        current_date: str,
        refresh: bool,
    ) -> dict[str, Any] | None:
        if refresh:
            return None
        path = self.task_cache_dir / f"{task.key}.json"
        if not path.exists():
            return None
        try:
            payload = read_json_file(path)
        except (OSError, json.JSONDecodeError):
            return None
        if payload.get("current_date") != current_date:
            return None
        return payload

    def save_success(self, task: DiscoveryTask, current_date: str, model: str, output: str) -> None:
        atomic_write_json(
            self.task_cache_dir / f"{task.key}.json",
            {
                "task": task.name,
                "model": model,
                "current_date": current_date,
                "saved_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "output": output,
            },
        )

    def delete_success(self, task: DiscoveryTask) -> None:
        path = self.task_cache_dir / f"{task.key}.json"
        if path.exists():
            path.unlink()

    def save_artifact(self, artifact: RunArtifact) -> None:
        base = f"{slugify(artifact.task_name)}.{slugify(artifact.model)}.{artifact.attempt}"
        raw_path = self.run_dir / f"{base}.txt"
        meta_path = self.run_dir / f"{base}.json"
        raw_path.write_text(artifact.output, encoding="utf-8")
        atomic_write_json(
            meta_path,
            {
                "task_name": artifact.task_name,
                "model": artifact.model,
                "attempt": artifact.attempt,
                "success": artifact.success,
                "error": artifact.error,
                "elapsed_seconds": round(artifact.elapsed_seconds, 2),
                "from_cache": artifact.from_cache,
                "timed_out": artifact.timed_out,
                "timeout_seconds": artifact.timeout_seconds,
                "partial_output_path": artifact.partial_output_path,
                "raw_task_output_path": artifact.raw_task_output_path,
                "parser_accepted": artifact.parser_accepted,
                "parser_diagnostics": artifact.parser_diagnostics,
                "raw_output_path": str(raw_path),
            },
        )

    def partial_output_path(self, task: DiscoveryTask, model: str, attempt: int) -> Path:
        return self.run_dir / f"partial_output_{slugify(model)}.log"

    def raw_task_output_path(self, task: DiscoveryTask) -> Path:
        return self.run_dir / f"raw_output_{slugify(task.name)}.txt"

    def append_partial_chunk(
        self,
        task: DiscoveryTask,
        model: str,
        attempt: int,
        chunk: str,
    ) -> tuple[Path, Path]:
        partial_path = self.partial_output_path(task, model, attempt)
        raw_task_path = self.raw_task_output_path(task)
        for path in (partial_path, raw_task_path):
            with path.open("a", encoding="utf-8") as handle:
                handle.write(chunk)
                handle.flush()
        return partial_path, raw_task_path

    def save_run_summary(self, payload: dict[str, Any]) -> None:
        atomic_write_json(self.run_dir / "run_summary.json", payload)


class JsonExtractor:
    def parse_payload(self, output: str) -> tuple[dict[str, Any] | None, list[str]]:
        warnings: list[str] = []
        diagnostics: dict[str, Any] = {
            "input_chars": len(output or ""),
            "candidate_blocks": 0,
            "repair_attempts": 0,
            "valid_blocks": 0,
            "salvaged_candidates": 0,
            "strategy": None,
        }
        self.last_diagnostics = diagnostics
        clean_output = self._strip_ansi(output or "")
        blocks = self._candidate_blocks(clean_output)
        diagnostics["candidate_blocks"] = len(blocks)

        for block in blocks:
            parsed = self._loads_with_repair(block, diagnostics)
            if parsed is None:
                continue
            diagnostics["valid_blocks"] += 1
            if isinstance(parsed, list):
                diagnostics["strategy"] = "array_payload"
                if diagnostics["repair_attempts"]:
                    warnings.append("Repaired JSON-like output before parsing.")
                return {"candidates": parsed, "metadata": {"json_shape": "array"}}, warnings
            if isinstance(parsed, dict):
                if self._looks_like_candidate(parsed):
                    warnings.append("Recovered a single candidate object from partial output.")
                    diagnostics["strategy"] = "single_candidate"
                    diagnostics["salvaged_candidates"] = 1
                    return {
                        "candidates": [parsed],
                        "metadata": {"json_shape": "single_candidate"},
                    }, warnings
                if self._has_candidate_container(parsed):
                    diagnostics["strategy"] = "container_payload"
                    diagnostics["salvaged_candidates"] = self._candidate_count(parsed)
                    if diagnostics["repair_attempts"]:
                        warnings.append("Repaired JSON-like output before parsing.")
                    return parsed, warnings
                if diagnostics["repair_attempts"]:
                    warnings.append("Repaired JSON-like output before parsing.")
                return parsed, warnings

        candidates = []
        for block in self._json_blocks(clean_output, nested=True):
            parsed = self._loads_with_repair(block, diagnostics)
            if parsed is None:
                continue
            if isinstance(parsed, dict) and self._looks_like_candidate(parsed):
                candidates.append(parsed)

        if candidates:
            warnings.append("Recovered complete candidate objects from partial output.")
            diagnostics["strategy"] = "salvaged_objects"
            diagnostics["salvaged_candidates"] = len(candidates)
            return {
                "candidates": candidates,
                "metadata": {
                    "json_shape": "salvaged_objects",
                    "partial_output_recovered": True,
                },
            }, warnings

        warnings.append("No valid JSON payload or candidate objects found.")
        diagnostics["strategy"] = "failed"
        return None, warnings

    def _strip_ansi(self, text: str) -> str:
        return re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", text)

    def _has_candidate_container(self, item: dict[str, Any]) -> bool:
        return any(
            isinstance(item.get(key), list)
            for key in ("government_hackathons", "candidates", "opportunities", "active_opportunities")
        )

    def _candidate_count(self, item: dict[str, Any]) -> int:
        total = 0
        for key in ("government_hackathons", "candidates", "opportunities", "active_opportunities"):
            value = item.get(key)
            if isinstance(value, list):
                total += len([entry for entry in value if isinstance(entry, dict)])
        return total

    def _candidate_blocks(self, text: str) -> list[str]:
        blocks: list[str] = []
        for match in re.finditer(r"```(?:json|JSON)?\s*([\s\S]*?)```", text):
            fenced = match.group(1).strip()
            if fenced:
                blocks.append(fenced)
        blocks.extend(self._json_blocks(text))
        blocks.extend(self._json_blocks(text, nested=True))
        deduped = list(dict.fromkeys(blocks))
        return sorted(deduped, key=len, reverse=True)

    def _loads_with_repair(self, block: str, diagnostics: dict[str, Any]) -> Any:
        variants = [block.strip()]
        repaired = self._repair_jsonish(block)
        if repaired and repaired not in variants:
            variants.append(repaired)
        for idx, variant in enumerate(variants):
            if idx:
                diagnostics["repair_attempts"] += 1
            try:
                return json.loads(variant)
            except json.JSONDecodeError:
                continue
        return None

    def _repair_jsonish(self, block: str) -> str | None:
        text = block.strip()
        if not text:
            return None
        text = re.sub(r"^```(?:json|JSON)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
        text = re.sub(r",(\s*[\]}])", r"\1", text)
        start_positions = [pos for pos in [text.find("{"), text.find("[")] if pos != -1]
        if start_positions:
            text = text[min(start_positions):]
        stack: list[str] = []
        in_string = False
        escape = False
        for char in text:
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char in "{[":
                stack.append(char)
            elif char in "}]":
                if stack and (stack[-1], char) in {("{", "}"), ("[", "]")}:
                    stack.pop()
        if in_string:
            text += '"'
        while stack:
            opener = stack.pop()
            text += "}" if opener == "{" else "]"
        return text

    def _looks_like_candidate(self, item: dict[str, Any]) -> bool:
        keys = set(item)
        signals = {
            "hackathon_name",
            "full_name",
            "event_type",
            "registration_url",
            "official_website",
            "official_event_page",
            "source_url",
            "current_status",
        }
        return len(keys & signals) >= 3

    def _json_blocks(self, text: str, nested: bool = False) -> list[str]:
        blocks: list[str] = []
        starts = range(len(text)) if nested else self._outer_starts(text)
        for start in starts:
            if text[start] not in "{[":
                continue
            block = self._balanced_from(text, start)
            if block:
                blocks.append(block)
                if not nested:
                    break
        return blocks

    def _outer_starts(self, text: str) -> list[int]:
        starts = []
        for match in re.finditer(r"[\{\[]", text):
            starts.append(match.start())
        return starts

    def _balanced_from(self, text: str, start: int) -> str | None:
        stack: list[str] = []
        in_string = False
        escape = False
        for idx in range(start, len(text)):
            char = text[idx]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char in "{[":
                stack.append(char)
            elif char in "}]":
                if not stack:
                    return None
                opener = stack.pop()
                if (opener, char) not in {("{", "}"), ("[", "]")}:
                    return None
                if not stack:
                    return text[start : idx + 1]
        return None


class DiscoveryPlanner:
    def __init__(self, current_date: str, max_candidates_per_task: int) -> None:
        self.current_date = current_date
        self.max_candidates_per_task = max_candidates_per_task

    def tasks(self) -> list[DiscoveryTask]:
        return [
            DiscoveryTask(
                name="strict-high-priority-competitions",
                category="strict_competition",
                priority=1,
                seed_urls=[
                    "https://idex.gov.in/challenges",
                    "https://www.drdo.gov.in/",
                    "https://www.isro.gov.in/",
                    "https://indiaai.gov.in/",
                    "https://aikosh.indiaai.gov.in/",
                    "https://www.sih.gov.in/",
                    "https://innovateindia.mygov.in/",
                    "https://www.mygov.in/",
                    "https://www.nic.in/",
                    "https://www.meity.gov.in/",
                ],
                queries=STRICT_SEARCH_QUERIES,
            ),
            DiscoveryTask(
                name="defence-space-ai-competitions",
                category="strict_competition",
                priority=2,
                seed_urls=[
                    "https://idex.gov.in/challenges",
                    "https://www.drdo.gov.in/",
                    "https://www.isro.gov.in/",
                    "https://indiaai.gov.in/",
                    "https://aikosh.indiaai.gov.in/",
                ],
                queries=[
                    "site:idex.gov.in challenge open",
                    "site:drdo.gov.in challenge registration",
                    "site:isro.gov.in hackathon registration",
                    "site:indiaai.gov.in ai challenge",
                    "site:aikosh.indiaai.gov.in competitions",
                ],
            ),
            DiscoveryTask(
                name="hackathon-coding-cyber-competitions",
                category="strict_competition",
                priority=3,
                seed_urls=[
                    "https://www.sih.gov.in/",
                    "https://www.mygov.in/",
                    "https://innovateindia.mygov.in/",
                    "https://www.nic.in/",
                    "https://www.meity.gov.in/",
                ],
                queries=[
                    "site:gov.in coding competition registration",
                    "site:gov.in ctf registration",
                    "site:gov.in cybersecurity competition registration",
                    "site:gov.in technical challenge problem statement",
                    "site:sih.gov.in registration",
                    "site:mygov.in hackathon",
                    "site:nic.in cyber challenge",
                ],
            ),
        ]

    def build_prompt(self, task: DiscoveryTask) -> str:
        return f"""
You are an evidence extraction worker for a deterministic opportunity pipeline.
Return JSON only. Do not score, rank, deduplicate, or decide final inclusion.

Validation date: {self.current_date}
Task: {task.name}
Category: {task.category}

Semantic intent:
Find only explicit Indian government-affiliated technical competitions that may
still accept new registrations, applications, or submissions on {self.current_date}.

Seed official URLs:
{json.dumps(task.seed_urls, indent=2)}

Search queries to execute or approximate:
{json.dumps(task.queries, indent=2)}

Allowed opportunity intent:
- hackathons
- AI challenges
- cybersecurity competitions
- defence innovation challenges
- coding competitions
- capture the flag competitions
- technical innovation challenges with visible competition structure

Forbidden primary intent:
- grants, funding calls, startup programs, incubators, accelerators, fellowships
- proposal calls, R&D funding solicitations, procurement notices, RFPs, tenders
- startup recognition, startup ecosystem, innovation ecosystem, cohorts, awards
- logo, slogan, mascot, essay, poster, quiz, photography, awareness contests

Extraction rules:
- Extract only facts visible in source evidence.
- Use null when a fact is missing.
- Do not invent deadlines, URLs, ministries, prizes, or future editions.
- Do not include "may qualify", "could be", or "possibly technical" records.
- Only include candidates with explicit competition structure: challenge,
  hackathon, competition, CTF, problem statement, team size, judging criteria,
  leaderboard, prototype submission, winner, prize, or grand finale.
- Put invalid or forbidden pages in "excluded" with the observed reason.
- Return at most {self.max_candidates_per_task} candidates.

Return ONLY strict JSON in this exact shape:
{{
  "candidates": [
    {{
      "event_type": "hackathon | ai_challenge | cybersecurity_competition | coding_competition | defence_innovation_challenge | technical_challenge | ctf | null",
      "hackathon_name": "official public name",
      "full_name": "complete official name if different or null",
      "current_status": "registration_open | submission_open | application_open | closed | archived | unknown",
      "registration_url": "direct apply/register URL or null",
      "submission_url": "direct submit URL or null",
      "official_website": "official organization URL",
      "official_event_page": "official event/challenge page URL",
      "source_url": "URL where open status/deadline was verified",
      "hosting_organization": "official organizer",
      "ministry": "ministry/department if known",
      "institution_type": "Central Government | State Government | Government Academic | PSU | Government Incubator",
      "platform": "portal/platform",
      "domain": "primary sector",
      "theme": "short theme",
      "focus_areas": ["area"],
      "deadline": "YYYY-MM-DD or null",
      "registration_close_date": "YYYY-MM-DD or null",
      "submission_close_date": "YYYY-MM-DD or null",
      "application_deadline": "YYYY-MM-DD or null",
      "eligibility_criteria": {{"summary": "verifiable summary"}},
      "team_size": {{"minimum": null, "maximum": null}},
      "submission_fee": {{"amount": 0, "currency": "INR", "display": "Free or Unknown"}},
      "prizes": {{"summary": "verifiable rewards only"}},
      "problem_statements": ["verifiable challenge/problem statement"],
      "tags": ["observed semantic signals"],
      "source_validation": {{
        "source_type": "official_government_portal | official_academic_portal | official_partner_registration",
        "official_confirmation_found": true,
        "official_open_keywords_found": ["short exact phrases"],
        "deadline_verified": true
      }}
    }}
  ],
  "excluded": [
    {{
      "name": "candidate name",
      "source_url": "checked source",
      "reason": "closed | not_government | unverifiable | stale_cycle | no_new_registration"
    }}
  ],
  "sources_scanned": ["url"],
  "search_queries_used": ["query"]
}}
""".strip()


class OpenCodeRunner:
    TRANSIENT_PATTERNS = [
        r"timeout",
        r"timed out",
        r"rate.?limit",
        r"temporar",
        r"overloaded",
        r"ECONNRESET",
        r"ETIMEDOUT",
        r"AI_RetryError",
    ]
    FATAL_PATTERNS = [
        r"ProviderInitError",
        r"AI_LoadAPIKeyError",
        r"NoSuchModelError",
        r"InvalidModelError",
        r"ModelNotFound",
    ]
    REFUSAL_PATTERNS = [
        r"\bI (?:can'?t|cannot|won'?t) (?:provide|help|assist)",
        r"\bunable to provide\b",
        r"无法给到相关内容",
    ]

    def __init__(
        self,
        models: list[str],
        checkpoint_store: CheckpointStore,
        retries: int,
        current_date: str,
        refresh_cache: bool,
        status_callback: Callable[[str, dict[str, Any]], None] | None = None,
        quiet: bool = False,
    ) -> None:
        self.models = models
        self.checkpoint_store = checkpoint_store
        self.retries = max(1, retries)
        self.current_date = current_date
        self.refresh_cache = refresh_cache
        self.status_callback = status_callback
        self.quiet = quiet

    def _emit(self, message: str) -> None:
        if not self.quiet:
            print(message)

    def _notify(self, event: str, **payload: Any) -> None:
        if self.status_callback:
            self.status_callback(event, payload)

    def run_task(
        self,
        task: DiscoveryTask,
        prompt: str,
        output_acceptor: Callable[[RunArtifact], bool] | None = None,
    ) -> tuple[str | None, list[RunArtifact]]:
        self._notify("task_start", task_name=task.name, task_category=task.category)
        cached_payload = self.checkpoint_store.load_success_payload(task, self.current_date, self.refresh_cache)
        cached = cached_payload.get("output") if cached_payload else None
        if cached:
            self._emit(f"  [{task.name}] cache hit")
            self._notify("cache_hit", task_name=task.name, model=cached_payload.get("model") if cached_payload else "cache")
            cached_model = cached_payload.get("model") if cached_payload else "cache"
            artifact = RunArtifact(task.name, cached_model or "cache", 0, True, cached, None, 0.0, from_cache=True)
            if output_acceptor:
                artifact.parser_accepted = output_acceptor(artifact)
            self.checkpoint_store.save_artifact(artifact)
            if artifact.parser_accepted is False:
                self._emit(f"  [{task.name}] cached output failed parser; rerunning fallback chain")
                self._notify("parser_rejected", task_name=task.name, model=cached_model or "cache")
                self.checkpoint_store.delete_success(task)
            else:
                self._notify("task_complete", task_name=task.name, success=True, from_cache=True)
                return cached, [artifact]

        artifacts: list[RunArtifact] = []
        for model in self.models:
            self._notify("model_selected", task_name=task.name, model=model)
            for attempt in range(1, self.retries + 1):
                timeout = self._model_timeout(model)
                artifact = self._run_once(task, model, attempt, prompt, timeout)
                artifacts.append(artifact)
                if artifact.success:
                    if output_acceptor:
                        artifact.parser_accepted = output_acceptor(artifact)
                    self.checkpoint_store.save_artifact(artifact)
                    if artifact.parser_accepted is False:
                        artifact.success = False
                        artifact.error = "Parser rejected model output; activating fallback"
                        self.checkpoint_store.save_artifact(artifact)
                        self._emit(f"  parser rejected {model}; activating fallback")
                        self._notify("parser_rejected", task_name=task.name, model=model)
                        break
                    self._notify("task_complete", task_name=task.name, success=True, model=model)
                    return artifact.output, artifacts
                self.checkpoint_store.save_artifact(artifact)
                if artifact.error and self._is_fatal(artifact.error + "\n" + artifact.output):
                    self._notify("fatal_error", task_name=task.name, model=model, error=artifact.error)
                    break
                if attempt < self.retries:
                    delay = min(20, 2 ** attempt)
                    self._emit(f"  retrying {model} for {task.name} in {delay}s")
                    self._notify("retrying", task_name=task.name, model=model, delay=delay)
                    time.sleep(delay)
            self._emit(f"  fallback activated after {model}")
            self._notify("fallback", task_name=task.name, model=model)
        self._notify("task_complete", task_name=task.name, success=False)
        return self._best_partial(artifacts), artifacts

    def _best_partial(self, artifacts: list[RunArtifact]) -> str | None:
        best_partial = max(artifacts, key=lambda item: len(item.output), default=None)
        if best_partial and best_partial.output:
            return best_partial.output
        return None

    def _model_timeout(self, model: str) -> int:
        return MODEL_TIMEOUTS.get(model, DEFAULT_MODEL_TIMEOUT)

    def _run_once(self, task: DiscoveryTask, model: str, attempt: int, prompt: str, timeout: int) -> RunArtifact:
        started = time.monotonic()
        self._emit(f"  [{task.name}] model={model} attempt={attempt}/{self.retries} timeout={timeout}s")
        self._notify("model_start", task_name=task.name, model=model, attempt=attempt, timeout=timeout)
        partial_path = self.checkpoint_store.partial_output_path(task, model, attempt)
        raw_task_path = self.checkpoint_store.raw_task_output_path(task)
        header = (
            f"\n\n--- task={task.name} model={model} attempt={attempt} "
            f"started={datetime.now(UTC).isoformat(timespec='seconds')} timeout={timeout}s ---\n"
        )
        self.checkpoint_store.append_partial_chunk(task, model, attempt, header)
        try:
            process = subprocess.Popen(
                ["opencode", "run", "-m", model, prompt],
                cwd=PROJECT_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
            )
            output, timed_out = self._stream_process_output(process, task, model, attempt, timeout)
            elapsed = time.monotonic() - started
            error = None
            success = process.returncode == 0 and not self._has_model_error(output)
            if timed_out:
                error = f"Timeout after {timeout}s"
                success = False
            elif process.returncode != 0:
                error = f"OpenCode exit code {process.returncode}"
            elif self._has_model_error(output):
                error = "OpenCode reported provider/model/API error"
            elif self._has_refusal(output):
                success = False
                error = "OpenCode returned a refusal/no-content response"
            print(f"    completed in {elapsed:.1f}s success={success}")
            return RunArtifact(
                task.name,
                model,
                attempt,
                success,
                output,
                error,
                elapsed,
                timed_out=timed_out,
                timeout_seconds=timeout,
                partial_output_path=str(partial_path),
                raw_task_output_path=str(raw_task_path),
            )
        except FileNotFoundError:
            return RunArtifact(
                task.name,
                model,
                attempt,
                False,
                "",
                "OpenCode CLI not found on PATH",
                time.monotonic() - started,
                timeout_seconds=timeout,
                partial_output_path=str(partial_path),
                raw_task_output_path=str(raw_task_path),
            )

    def _stream_process_output(
        self,
        process: subprocess.Popen[bytes],
        task: DiscoveryTask,
        model: str,
        attempt: int,
        timeout: int,
    ) -> tuple[str, bool]:
        output_parts: list[str] = []
        timed_out = False
        selector = selectors.DefaultSelector()
        if process.stdout is not None:
            selector.register(process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 and process.poll() is None:
                timed_out = True
                process.kill()
            events = selector.select(timeout=0.25)
            for key, _ in events:
                chunk = os.read(key.fileobj.fileno(), 8192)
                if not chunk:
                    try:
                        selector.unregister(key.fileobj)
                    except KeyError:
                        pass
                    continue
                text = chunk.decode("utf-8", errors="replace")
                output_parts.append(text)
                self.checkpoint_store.append_partial_chunk(task, model, attempt, text)
            if process.poll() is not None:
                for key in list(selector.get_map().values()):
                    try:
                        while True:
                            chunk = os.read(key.fileobj.fileno(), 8192)
                            if not chunk:
                                break
                            text = chunk.decode("utf-8", errors="replace")
                            output_parts.append(text)
                            self.checkpoint_store.append_partial_chunk(task, model, attempt, text)
                    except OSError:
                        pass
                break
        selector.close()
        if timed_out:
            self._emit(f"    timed out after {timeout}s; partial output preserved")
            self._notify("model_timeout", task_name=task.name, model=model, timeout=timeout)
        return "".join(output_parts), timed_out

    def _has_model_error(self, output: str) -> bool:
        return any(re.search(pattern, output, flags=re.I) for pattern in self.FATAL_PATTERNS)

    def _has_refusal(self, output: str) -> bool:
        return any(re.search(pattern, output, flags=re.I) for pattern in self.REFUSAL_PATTERNS)

    def _is_fatal(self, output: str) -> bool:
        return any(re.search(pattern, output, flags=re.I) for pattern in self.FATAL_PATTERNS)


class ValidationEngine:
    def __init__(
        self,
        current_date: str,
        live_validation: bool = True,
        timeout: int = 12,
        max_bytes: int = 250_000,
    ) -> None:
        self.current_date = parse_date(current_date) or date.today()
        self.live_validation = live_validation
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.page_cache = PageFingerprintCache()
        self.hard_negative_gate = HardNegativeGate()
        self.competition_signal_engine = CompetitionSignalEngine()

    def validate(self, record: OpportunityRecord) -> OpportunityRecord:
        probes: list[PageProbe] = []
        urls = self._urls_to_probe(record)
        if self.live_validation:
            for url in urls[:3]:
                probes.append(self._probe_url(url))

        official_source = any(
            is_trusted_government_url(url)
            for url in [
                record.source_url,
                record.official_event_page,
                record.official_website,
            ]
        )
        registration_external = is_external_registration_url(record.registration_url)
        accessible_probe = next((probe for probe in probes if probe.ok), None)
        merged_text = " ".join(probe.text_excerpt.lower() for probe in probes)
        existing_open = [str(item) for item in record.source_validation.official_open_keywords_found]
        open_keywords = sorted(set(existing_open + self._keywords_found(merged_text, OPEN_KEYWORDS)))
        observed_closed_keywords = sorted(set(self._keywords_found(merged_text, CLOSED_KEYWORDS)))
        existing_form_detected = bool(
            record.source_validation.form_detected or record.source_validation.registration_form_detected
        )
        existing_auth_wall = bool(record.source_validation.auth_wall_detected)
        probed_form_detected = any(probe.form_detected for probe in probes)
        probed_auth_wall = any(probe.auth_wall_detected for probe in probes)
        form_detected = probed_form_detected or (
            existing_form_detected and (not self.live_validation or not probes or not accessible_probe)
        )
        auth_wall = probed_auth_wall or (
            existing_auth_wall and (not self.live_validation or not probes or not accessible_probe)
        )
        zombie = any(probe.zombie_page_suspected for probe in probes) or self._old_cycle_url(record)
        deadline_date = self._best_deadline(record)
        deadline_future = deadline_date is None or deadline_date >= self.current_date
        status_active = record.current_status in ACTIVE_STATUSES
        hard_closed = [keyword for keyword in observed_closed_keywords if keyword in HARD_CLOSED_KEYWORDS]
        closed_keywords = hard_closed if hard_closed else (
            [] if open_keywords and deadline_future else observed_closed_keywords
        )

        errors: list[str] = []
        if not official_source:
            errors.append("No trusted official government or academic source URL.")
        if self.live_validation and urls and not accessible_probe:
            errors.append("No probed URL returned an accessible page.")
        if deadline_date and deadline_date < self.current_date:
            errors.append(f"Deadline {deadline_date.isoformat()} is before {self.current_date.isoformat()}.")
        if closed_keywords:
            errors.append("Closed lifecycle keywords found on probed page.")
        if zombie:
            errors.append("Zombie or stale-cycle page suspected.")

        registration_verified = bool(accessible_probe) or (
            not self.live_validation and bool(record.registration_url or record.source_url)
        )
        workflow_detected = form_detected or (registration_external and bool(record.registration_url))
        workflow_with_deadline = workflow_detected and deadline_date is not None and deadline_future and status_active
        official_confirmation = bool(open_keywords) or workflow_with_deadline or (
            not self.live_validation and (status_active or bool(existing_open))
        )
        accepting_entries = deadline_future and status_active and official_source and not closed_keywords and not zombie
        if self.live_validation:
            accepting_entries = accepting_entries and bool(open_keywords) and deadline_date is not None

        record.source_validation.source_url = record.source_validation.source_url or record.source_url
        record.source_validation.source_type = record.source_validation.source_type or self._source_type(record)
        record.source_validation.registration_page_verified = registration_verified
        record.source_validation.registration_page_status_code = accessible_probe.status_code if accessible_probe else None
        record.source_validation.deadline_verified = deadline_date is not None and deadline_future
        record.source_validation.official_confirmation_found = official_confirmation
        record.source_validation.official_open_keywords_found = open_keywords
        record.source_validation.closed_keywords_found = closed_keywords
        record.source_validation.form_detected = form_detected
        record.source_validation.registration_form_detected = form_detected
        record.source_validation.auth_wall_detected = auth_wall
        record.source_validation.zombie_page_suspected = zombie
        record.source_validation.final_url = accessible_probe.final_url if accessible_probe else None
        record.source_validation.validation_errors = errors

        record.is_open_for_new_registration = accepting_entries
        record.is_open_for_submission = accepting_entries and record.current_status == "submission_open"
        record.confidence_score, record.confidence_reasons = self._score(
            record=record,
            official_source=official_source,
            accessible=bool(accessible_probe) or not self.live_validation,
            deadline_future=deadline_future,
            open_keywords=open_keywords,
            closed_keywords=closed_keywords,
            form_detected=form_detected,
            auth_wall=auth_wall,
            workflow_detected=workflow_detected,
            accepting_entries=accepting_entries,
            zombie=zombie,
        )
        record.verification_state = self._verification_state(record, accepting_entries, errors)
        return record

    def _urls_to_probe(self, record: OpportunityRecord) -> list[str]:
        ordered = [
            record.registration_url,
            record.official_event_page,
            record.source_url,
            record.official_website,
            record.submission_url,
        ]
        seen: set[str] = set()
        urls: list[str] = []
        for url in ordered:
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
        return urls

    def _probe_url(self, url: str) -> PageProbe:
        cached = self.page_cache.load_if_fresh(url)
        if cached:
            return cached
        started = time.monotonic()
        request = Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 OpportunityIntelligenceBot/1.0 "
                    "(validation; contact=local)"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read(self.max_bytes)
                charset = response.headers.get_content_charset() or "utf-8"
                markup = raw.decode(charset, errors="replace")
                text = strip_tags(markup)
                lower_markup = markup.lower()
                lower_text = text.lower()
                probe = PageProbe(
                    url=url,
                    final_url=canonicalize_url(response.geturl()),
                    status_code=getattr(response, "status", None),
                    ok=200 <= int(getattr(response, "status", 0) or 0) < 400,
                    elapsed_seconds=time.monotonic() - started,
                    text_excerpt=text[:4000],
                    raw_excerpt=markup[:4000],
                    open_keywords=self._keywords_found(lower_text, OPEN_KEYWORDS),
                    closed_keywords=self._keywords_found(lower_text, CLOSED_KEYWORDS),
                    form_detected=self._detect_application_workflow(lower_markup, lower_text),
                    auth_wall_detected=self._detect_auth_application_workflow(lower_text),
                    zombie_page_suspected=self._zombie_text(lower_text, url),
                )
                shallow_text = f"{url} {probe.text_excerpt}"
                if self.hard_negative_gate.should_reject(shallow_text):
                    probe.ok = False
                    probe.error = "hard_negative_gate_rejected_shallow_page"
                elif not self.competition_signal_engine.passes(shallow_text):
                    probe.ok = False
                    probe.error = "low_competition_signal_shallow_page"
                self.page_cache.save(
                    url,
                    probe,
                    raw=markup,
                    headers={key: value for key, value in response.headers.items()},
                )
                return probe
        except HTTPError as exc:
            return PageProbe(
                url=url,
                final_url=canonicalize_url(exc.url),
                status_code=exc.code,
                elapsed_seconds=time.monotonic() - started,
                error=f"HTTP {exc.code}",
            )
        except IncompleteRead as exc:
            # Handle incomplete HTTP responses gracefully
            return PageProbe(
                url=url,
                elapsed_seconds=time.monotonic() - started,
                error=f"IncompleteRead: {exc.args[0] if exc.args else 'partial response'}",
            )
        except (HTTPException, URLError, TimeoutError, OSError) as exc:
            return PageProbe(
                url=url,
                elapsed_seconds=time.monotonic() - started,
                error=str(exc),
            )

    def _keywords_found(self, text: str, keywords: list[str]) -> list[str]:
        return [keyword for keyword in keywords if keyword in text]

    def _detect_application_workflow(self, lower_markup: str, lower_text: str) -> bool:
        if any(keyword in lower_text for keyword in HARD_CLOSED_KEYWORDS):
            return False
        active_endpoint = re.search(
            r"""(?:href|action)=["'][^"']*(?:apply|register|registration|submit|submission|application|challenge)[^"']*["']""",
            lower_markup,
        )
        has_submit_control = (
            "type=\"submit\"" in lower_markup
            or "type='submit'" in lower_markup
            or "<button" in lower_markup
        )
        has_intake_context = any(
            keyword in lower_text
            for keyword in (
                "submit application",
                "start application",
                "registration form",
                "application form",
                "apply now",
                "register now",
                "participate now",
            )
        )
        active_form = "<form" in lower_markup and (
            has_submit_control and has_intake_context
        )
        return bool(active_form or active_endpoint)

    def _detect_auth_application_workflow(self, lower_text: str) -> bool:
        if any(keyword in lower_text for keyword in HARD_CLOSED_KEYWORDS):
            return False
        has_auth = any(keyword in lower_text for keyword in AUTH_KEYWORDS)
        has_apply_context = any(
            keyword in lower_text
            for keyword in ("apply", "register", "registration", "submit", "application")
        )
        return has_auth and has_apply_context

    def _best_deadline(self, record: OpportunityRecord) -> date | None:
        for value in [
            record.deadline,
            record.application_deadline,
            record.registration_close_date,
            record.submission_close_date,
            record.proposal_deadline,
        ]:
            parsed = parse_date(value)
            if parsed:
                return parsed
        return None

    def _old_cycle_url(self, record: OpportunityRecord) -> bool:
        current_year = self.current_date.year
        urls = " ".join(
            url or ""
            for url in [
                record.registration_url,
                record.official_event_page,
                record.source_url,
            ]
        )
        years = [int(year) for year in re.findall(r"\b20\d{2}\b", urls)]
        return bool(years and max(years) < current_year)

    def _zombie_text(self, text: str, url: str) -> bool:
        years = [int(year) for year in re.findall(r"\b20\d{2}\b", url + " " + text[:1000])]
        stale_year = bool(years and max(years) < self.current_date.year)
        closed_signal = any(keyword in text for keyword in CLOSED_KEYWORDS)
        open_signal = any(keyword in text for keyword in OPEN_KEYWORDS)
        return stale_year and closed_signal and not open_signal

    def _source_type(self, record: OpportunityRecord) -> str:
        if is_trusted_government_url(record.source_url):
            return "official_government_portal"
        if is_trusted_government_url(record.official_website):
            return "official_government_portal"
        if is_trusted_government_url(record.official_event_page):
            return "official_government_portal"
        if record.official_website and hostname(record.official_website).endswith(".ac.in"):
            return "official_academic_portal"
        return "unverified_source"

    def _verification_state(
        self,
        record: OpportunityRecord,
        accepting_entries: bool,
        errors: list[str],
    ) -> str:
        if (
            accepting_entries
            and not errors
            and self.live_validation
            and record.source_validation.registration_form_detected
            and not record.source_validation.auth_wall_detected
            and record.confidence_score >= 95
        ):
            return "fully_verified"
        if accepting_entries and not errors:
            return "verified"
        if errors and any("Deadline" in error or "Closed" in error or "Zombie" in error for error in errors):
            return "rejected"
        return "needs_review"

    def _score(
        self,
        record: OpportunityRecord,
        official_source: bool,
        accessible: bool,
        deadline_future: bool,
        open_keywords: list[str],
        closed_keywords: list[str],
        form_detected: bool,
        auth_wall: bool,
        workflow_detected: bool,
        accepting_entries: bool,
        zombie: bool,
    ) -> tuple[int, list[str]]:
        score = 0
        cap = 99
        reasons: list[str] = []
        if official_source:
            score += 18
            reasons.append("trusted official source")
        if accessible:
            score += 10
            reasons.append("source accessible or live validation skipped")
        if deadline_future:
            score += 12
            reasons.append("deadline is current or unknown")
        if open_keywords:
            score += 12
            reasons.append("open-registration language found")
        if form_detected:
            score += 5
            reasons.append("form or submit affordance detected")
        if record.current_status in ACTIVE_STATUSES:
            score += 8
            reasons.append("active status normalized")
        if not open_keywords and self.live_validation:
            score -= 10
            reasons.append("no open-status language found during live validation")
        if closed_keywords:
            score -= 35
            reasons.append("closed lifecycle language found")
        if zombie:
            score -= 30
            reasons.append("stale-cycle or zombie page suspected")
        if not official_source:
            score = min(score, 55)
            reasons.append("official source missing")
        if self.live_validation and not accessible:
            score = min(score, 60)
            reasons.append("live page access failed")
        if not self.live_validation:
            cap = min(cap, 89)
            reasons.append("live validation skipped")
        if auth_wall:
            cap = min(cap, 96)
            reasons.append("auth-wall workflow ambiguity")
        if not form_detected:
            cap = min(cap, 95)
            reasons.append("active form or apply endpoint missing")
        if not workflow_detected:
            cap = min(cap, 92)
            reasons.append("intake workflow not detected")
        if not accepting_entries:
            cap = min(cap, 90)
            reasons.append("new-user intake not fully confirmed")
        return max(0, min(cap, score)), reasons


class NormalizationEngine:
    def __init__(self, current_date: str, min_confidence: int, live_validation: bool) -> None:
        self.current_date = current_date
        self.min_confidence = min_confidence
        self.validator = ValidationEngine(current_date, live_validation=live_validation)
        self.hard_negative_gate = HardNegativeGate()
        self.competition_signal_engine = CompetitionSignalEngine()

    def normalize_payloads(self, payloads: list[dict[str, Any]]) -> dict[str, Any]:
        raw_candidates: list[tuple[dict[str, Any], str, dict[str, Any]]] = []
        excluded: list[dict[str, Any]] = []
        sources_scanned: set[str] = set()
        queries_used: set[str] = set()
        metadata_in: list[dict[str, Any]] = []

        for payload in payloads:
            payload_sources = set(str(item) for item in ensure_list(payload.get("sources_scanned")) if item)
            payload_queries = set(str(item) for item in ensure_list(payload.get("search_queries_used")) if item)
            metadata = payload.get("metadata")
            if isinstance(metadata, dict):
                metadata_in.append(metadata)
                payload_sources.update(str(item) for item in ensure_list(metadata.get("sources_scanned")) if item)
                payload_queries.update(str(item) for item in ensure_list(metadata.get("search_queries_used")) if item)

            sources_scanned.update(payload_sources)
            queries_used.update(payload_queries)
            context = {
                "task_name": metadata.get("task_name") if isinstance(metadata, dict) else None,
                "task_category": metadata.get("task_category") if isinstance(metadata, dict) else None,
                "discovery_model": metadata.get("discovery_model") if isinstance(metadata, dict) else None,
                "sources_scanned": sorted(payload_sources),
                "search_queries_used": sorted(payload_queries),
            }

            for key in ("government_hackathons", "candidates", "opportunities", "active_opportunities"):
                for item in ensure_list(payload.get(key)):
                    if isinstance(item, dict):
                        raw_candidates.append((item, "active_candidate", context))

            for item in ensure_list(payload.get("borderline_opportunities")):
                if isinstance(item, dict):
                    raw_candidates.append((item, "borderline_candidate", context))

            for item in ensure_list(payload.get("excluded")) + ensure_list(payload.get("excluded_opportunities")):
                if isinstance(item, dict):
                    if self._should_preserve_excluded_candidate(item):
                        raw_candidates.append((item, "excluded_candidate", context))
                    else:
                        excluded.append(item)

        decisions = self._normalize_candidates(raw_candidates)
        fully_verified = [decision.record for decision in decisions if decision.bucket == "fully_verified"]
        likely_active = [decision.record for decision in decisions if decision.bucket == "likely_active"]
        active = fully_verified + likely_active
        borderline = [decision.record for decision in decisions if decision.bucket == "borderline"]
        archived = [decision.record for decision in decisions if decision.bucket == "archived"]
        rejected = [decision.record for decision in decisions if decision.bucket == "rejected"]

        for record in fully_verified + likely_active + borderline + archived + rejected:
            for url in [
                record.source_url,
                record.official_event_page,
                record.official_website,
                record.registration_url,
            ]:
                if url:
                    sources_scanned.add(url)

        for record in rejected:
            excluded.append(
                {
                    "name": record.hackathon_name,
                    "source_url": record.source_url,
                    "reason": record.exclusion_reason or "failed_validation",
                    "confidence_score": record.confidence_score,
                }
            )

        active_dump = [self._dump_record(record) for record in active]
        excluded_dump = self._dedupe_excluded(excluded)
        domains_scanned_count = len({hostname(url) for url in sources_scanned if url})

        return {
            "government_hackathons": active_dump,
            "excluded_opportunities": excluded_dump,
            "metadata": {
                "search_date": self.current_date,
                "current_date_used_for_validation": self.current_date,
                "total_active_hackathons": len(active_dump),
                "total_excluded": len(excluded_dump),
                "total_candidates_discovered": len(raw_candidates),
                "sources_scanned": sorted(sources_scanned)[:MAX_STORED_SOURCES],
                "domains_scanned_count": domains_scanned_count,
                "search_queries_used": sorted(queries_used)[:MAX_STORED_SEARCH_QUERIES],
                "normalization_engine": "rules_first_strict_triage",
                "triage_only_mode": TRIAGE_ONLY_MODE,
                "max_crawl_depth": MAX_CRAWL_DEPTH,
                "live_validation_enabled": self.validator.live_validation,
                "data_quality_notes": [
                    "Rules-first strict competition discovery.",
                    "Hard negatives and low competition-signal candidates are rejected before live validation.",
                    "LLM output is used only as factual extraction input.",
                ][:MAX_STORED_DIAGNOSTICS],
            },
        }

    def _should_preserve_excluded_candidate(self, item: dict[str, Any]) -> bool:
        text = normalize_space(json.dumps(item, ensure_ascii=False, sort_keys=True).lower())
        if self.hard_negative_gate.should_reject(text):
            return False
        score, _ = self.competition_signal_engine.score(text)
        if score < self.competition_signal_engine.threshold:
            return False
        has_governmentish_url = any(
            is_trusted_government_url(item.get(key))
            for key in ("source_url", "official_event_page", "official_website", "registration_url")
        )
        status = str(item.get("current_status") or item.get("status") or "").lower()
        active_semantics = any(status_value in status for status_value in ACTIVE_STATUSES) or any(
            phrase in text for phrase in OPEN_KEYWORDS
        )
        return bool(has_governmentish_url and active_semantics)

    def _is_archived_reason(self, reason: str) -> bool:
        return any(keyword in reason for keyword in ARCHIVED_REASON_KEYWORDS)

    def _normalize_candidates(
        self,
        raw_candidates: list[tuple[dict[str, Any], str, dict[str, Any]]],
    ) -> list[CandidateDecision]:
        deduped: dict[str, CandidateDecision] = {}
        alias_to_key: dict[str, str] = {}
        for raw, source_bucket, context in raw_candidates:
            early_rejection = self._early_rejection_reason(raw, context)
            if early_rejection:
                fallback = OpportunityRecord(
                    hackathon_name=str(raw.get("hackathon_name") or raw.get("name") or "Rejected Candidate"),
                    source_url=raw.get("source_url") or raw.get("official_event_page") or raw.get("registration_url"),
                    confidence_score=0,
                    exclusion_reason=early_rejection,
                )
                self._upsert_decision(
                    deduped,
                    alias_to_key,
                    CandidateDecision("rejected", fallback, early_rejection),
                )
                continue
            raw = self._with_discovery_provenance(raw, context)
            try:
                record = OpportunityRecord.model_validate(raw)
            except ValidationError as exc:
                fallback = OpportunityRecord(
                    hackathon_name=str(raw.get("hackathon_name") or raw.get("name") or "Invalid Candidate"),
                    source_url=raw.get("source_url") or raw.get("official_event_page") or raw.get("registration_url"),
                    confidence_score=0,
                    exclusion_reason=f"schema_validation_failed: {exc.errors()[0].get('msg')}",
                )
                decision = CandidateDecision("rejected", fallback, fallback.exclusion_reason or "schema_validation_failed")
                self._upsert_decision(deduped, alias_to_key, decision)
                continue

            record.date_searched = record.date_searched or self.current_date
            record = self.validator.validate(record)
            decision = self._classify(record, source_bucket)
            self._upsert_decision(deduped, alias_to_key, decision)

        return sorted(deduped.values(), key=lambda item: (-item.record.confidence_score, item.record.hackathon_name))

    def _early_rejection_reason(self, raw: dict[str, Any], context: dict[str, Any]) -> str | None:
        text = self._raw_candidate_text(raw, context)
        blocked = self.hard_negative_gate.blocked_terms(text)
        if blocked:
            return f"hard_negative_gate:{','.join(blocked[:4])}"
        normalized = normalize_for_gate(text)
        if re.search(r"(?<!\w)startup(?!\w)", normalized, flags=re.UNICODE):
            return "startup_program_or_scheme"
        score, signals = self.competition_signal_engine.score(text)
        if score < self.competition_signal_engine.threshold:
            return f"low_competition_signal:{score:.2f}"
        event_type = self._raw_event_type(raw)
        if event_type not in VALID_TYPES:
            return f"invalid_event_type:{event_type or 'missing'}"
        if not signals:
            return "explicit_competition_structure_absent"
        return None

    def _raw_candidate_text(self, raw: dict[str, Any], context: dict[str, Any]) -> str:
        compact = {
            key: raw.get(key)
            for key in (
                "hackathon_name",
                "full_name",
                "event_type",
                "current_status",
                "registration_url",
                "submission_url",
                "official_website",
                "official_event_page",
                "source_url",
                "hosting_organization",
                "ministry",
                "platform",
                "domain",
                "theme",
                "focus_areas",
                "problem_statements",
                "eligibility_criteria",
                "team_size",
                "prizes",
                "tags",
                "source_validation",
                "reason",
                "exclusion_reason",
            )
            if raw.get(key) not in (None, "", [], {})
        }
        return normalize_space(
            json.dumps(
                {
                    "candidate": compact,
                    "context": {
                        "sources_scanned": context.get("sources_scanned", [])[:MAX_STORED_SOURCES],
                        "search_queries_used": context.get("search_queries_used", [])[:MAX_STORED_SEARCH_QUERIES],
                    },
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )

    def _raw_event_type(self, raw: dict[str, Any]) -> str:
        return OpportunityRecord._normalize_event_type(raw.get("event_type"))

    def _with_discovery_provenance(self, raw: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        item = dict(raw)
        if not item.get("hackathon_name") and item.get("name"):
            item["hackathon_name"] = item.get("name")
        if not item.get("source_url"):
            item["source_url"] = item.get("official_event_page") or item.get("registration_url") or item.get("official_website")
        provenance = ensure_dict(item.get("discovery_provenance"))
        models = set(str(model) for model in ensure_list(provenance.get("discovered_by_models")) if model)
        if context.get("discovery_model"):
            models.add(str(context["discovery_model"]))
        source_domains = set(str(domain) for domain in ensure_list(provenance.get("source_domains")) if domain)
        urls = list(context.get("sources_scanned") or [])
        urls.extend(
            [
                item.get("source_url"),
                item.get("official_event_page"),
                item.get("official_website"),
                item.get("registration_url"),
            ]
        )
        for url in urls:
            domain = hostname(canonicalize_url(url))
            if domain:
                source_domains.add(domain)
        query_count = ensure_int(provenance.get("discovery_query_count"), 0)
        query_count = max(query_count, len(context.get("search_queries_used") or []))
        provenance["discovered_by_models"] = sorted(models)
        provenance["source_domains"] = sorted(source_domains)
        provenance["discovery_query_count"] = query_count
        provenance["consensus_strength"] = consensus_strength(list(models))
        item["discovery_provenance"] = provenance
        search_metadata = ensure_dict(item.get("search_metadata"))
        for key in ("task_name", "task_category", "discovery_model"):
            if context.get(key):
                search_metadata.setdefault(key, context[key])
        if context.get("search_queries_used"):
            search_metadata.setdefault("search_queries_used", context["search_queries_used"])
        item["search_metadata"] = search_metadata
        return item

    def _classify(self, record: OpportunityRecord, source_bucket: str) -> CandidateDecision:
        record.classification_tier = "borderline"
        if not self._has_minimum_identity(record):
            return self._reject(record, "missing_identity_or_url")

        raw_reason = str(record.exclusion_reason or getattr(record, "reason", "") or "").strip().lower()
        if source_bucket == "excluded_candidate" and self._is_archived_reason(raw_reason):
            return self._archived(record, raw_reason or "archived_or_closed_source")
        if record.current_status in {"archived", "completed", "closed", "finalist", "judging"} or record.current_status.endswith("_closed"):
            return self._archived(record, f"current_status_{record.current_status}")
        if "proposal" in str(record.current_status).lower():
            return self._reject(record, "proposal_call_status_not_competition_intake")

        official_source = self._has_government_source(record)
        government_or_ecosystem = official_source or self._has_government_or_ecosystem_affiliation(record)
        deterministic_type, type_reason = self._deterministic_event_type(record)
        if deterministic_type == "reject":
            return self._reject(record, type_reason)
        record.event_type = deterministic_type
        if record.event_type not in VALID_TYPES:
            return self._reject(record, f"invalid_event_type:{record.event_type}")
        semantic_failure = self._semantic_purity_failure(record)
        blocked = self.hard_negative_gate.blocked_terms(self._semantic_text(record))
        if blocked:
            return self._reject(record, f"hard_negative_gate:{','.join(blocked[:4])}")
        competition_score, _ = self.competition_signal_engine.score(self._semantic_text(record))
        if competition_score < self.competition_signal_engine.threshold:
            return self._reject(record, f"low_competition_signal:{competition_score:.2f}")
        if self._is_proposal_or_r_and_d_only(record):
            return self._reject(record, "proposal_or_r_and_d_call_not_hackathon")
        if self._is_school_awareness_only(record):
            return self._reject(record, "school_awareness_category_not_hackathon")
        # Filter pure startup/seed/funding listings while preserving startup challenges/hackathons.
        if self._is_startup_seed_funding(record):
            return self._reject(record, "startup_or_funding_related")
        if semantic_failure and semantic_failure.startswith("non_technical_creative_contest"):
            return self._reject(record, semantic_failure)
        if self._deadline_is_past(record):
            return self._archived(record, "deadline_closed")
        if record.source_validation.zombie_page_suspected:
            return self._archived(record, "zombie_or_stale_page")
        if record.source_validation.closed_keywords_found:
            return self._archived(record, "closed_lifecycle_keywords_detected")
        if not government_or_ecosystem:
            return self._reject(record, "not_government_or_ecosystem_affiliated")

        tier_score = self._recall_tier_score(record, official_source)
        current_cycle = self._has_current_cycle_evidence(record)
        ecosystem_relevant = self._is_ecosystem_relevant(record)
        active_intake_issue = self._active_intake_issue(record)

        procurement_or_grant_issue = self._procurement_or_grant_ambiguity(record)
        if semantic_failure and not ecosystem_relevant:
            return self._reject(record, semantic_failure)

        if self._is_fully_verified(record, official_source) and not self._is_low_priority_record(record):
            return self._tier(record, "fully_verified", "official_source_active_workflow_and_deadline_confirmed")
        if (
            official_source
            and current_cycle
            and tier_score >= max(62, self.min_confidence - 10)
            and semantic_failure != "missing_strong_technical_signal"
            and not procurement_or_grant_issue
        ):
            reason = active_intake_issue or "strong_official_current_cycle_evidence"
            return self._tier(record, "likely_active", reason)
        if TRIAGE_ONLY_MODE:
            return self._reject(record, active_intake_issue or semantic_failure or "triage_mode_rejected")
        if self._borderline_allowed(record, official_source, active_intake_issue, procurement_or_grant_issue):
            return self._tier(record, "borderline", active_intake_issue or "registration_ambiguity")
        return self._reject(record, semantic_failure or active_intake_issue or "insufficient_recall_signals")

    def _borderline_allowed(
        self,
        record: OpportunityRecord,
        official_source: bool,
        active_intake_issue: str | None,
        procurement_or_grant_issue: str | None,
    ) -> bool:
        if procurement_or_grant_issue:
            return False
        text = self._semantic_text(record)
        blocked = self.hard_negative_gate.blocked_terms(text)
        if blocked:
            return False
        score, _ = self.competition_signal_engine.score(text)
        return bool(
            official_source
            and active_intake_issue
            and score >= self.competition_signal_engine.threshold
            and record.event_type in VALID_TYPES
        )

    def _is_low_priority_record(self, record: OpportunityRecord) -> bool:
        urls = [
            record.source_url,
            record.official_event_page,
            record.official_website,
            record.registration_url,
        ]
        hosts = [hostname(url) for url in urls if url]
        return any(
            host == domain or host.endswith("." + domain)
            for host in hosts
            for domain in LOW_PRIORITY_DOMAINS
            if "." in domain
        )

    def _is_fully_verified(self, record: OpportunityRecord, official_source: bool) -> bool:
        validation = record.source_validation
        has_apply_cta = self._has_apply_cta(record)
        return bool(
            official_source
            and validation.registration_page_verified
            and validation.deadline_verified
            and validation.official_confirmation_found
            and not validation.auth_wall_detected
            and (validation.registration_form_detected or has_apply_cta or self._uses_external_intake(record))
            and (record.is_open_for_new_registration or record.is_open_for_submission)
        )

    def _tier(self, record: OpportunityRecord, tier: str, reason: str) -> CandidateDecision:
        record.classification_tier = tier
        record.tier_reason = reason
        record.verification_state = tier
        if tier == "fully_verified":
            record.confidence_score = max(record.confidence_score, 90)
        elif tier == "likely_active":
            record.confidence_score = max(70, min(record.confidence_score, 89))
        elif tier == "borderline":
            record.confidence_score = max(40, min(record.confidence_score, 69))
        if tier == "borderline":
            record.borderline_reason = reason
        if reason not in record.confidence_reasons:
            record.confidence_reasons.append(reason)
        self._apply_review_flags(record)
        return CandidateDecision(tier, record, reason)

    def _archived(self, record: OpportunityRecord, reason: str) -> CandidateDecision:
        record.classification_tier = "archived"
        record.tier_reason = reason
        record.verification_state = "archived"
        record.confidence_score = max(record.confidence_score, 80)
        record.exclusion_reason = None
        if reason not in record.confidence_reasons:
            record.confidence_reasons.append(reason)
        self._apply_review_flags(record)
        return CandidateDecision("archived", record, reason)

    def _reject(self, record: OpportunityRecord, reason: str) -> CandidateDecision:
        record.classification_tier = "rejected"
        record.tier_reason = reason
        record.verification_state = "rejected"
        record.exclusion_reason = reason
        self._apply_review_flags(record)
        return CandidateDecision("rejected", record, reason)

    def _apply_review_flags(self, record: OpportunityRecord) -> None:
        record.admin_review_recommended = 60 <= record.confidence_score <= 89 and record.classification_tier != "archived"
        record.human_verification_needed = record.classification_tier in {"likely_active", "borderline"} or record.admin_review_recommended
        record.blue_tick_eligible = record.classification_tier == "fully_verified" and record.confidence_score >= 90

    def _has_apply_cta(self, record: OpportunityRecord) -> bool:
        open_text = " ".join(str(item).lower() for item in record.source_validation.official_open_keywords_found)
        return any(
            phrase in open_text
            for phrase in (
                "apply now",
                "challenge open",
                "registration open",
                "application open",
                "submission open",
                "submit",
                "register",
            )
        )

    def _recall_tier_score(self, record: OpportunityRecord, official_source: bool) -> int:
        validation = record.source_validation
        text = self._semantic_text(record)
        score = 0
        if official_source:
            score += 24
        elif self._has_government_or_ecosystem_affiliation(record):
            score += 14
        if self._has_current_cycle_evidence(record):
            score += 16
        if validation.registration_page_verified:
            score += 10
        if validation.registration_form_detected:
            score += 10
        elif validation.auth_wall_detected or self._uses_external_intake(record):
            score += 6
        if validation.official_confirmation_found or validation.official_open_keywords_found:
            score += 12
        if validation.deadline_verified:
            score += 12
        elif not record.deadline:
            score += 4
        if self._matched_keywords(text, STRONG_TECHNICAL_SIGNALS):
            score += 8
        if self._is_ecosystem_relevant(record):
            score += 8
        if len(ensure_list(record.discovery_provenance.get("source_domains"))) >= 2:
            score += 6
        if record.confidence_score >= 80:
            score += 6
        elif record.confidence_score >= 65:
            score += 3
        return min(100, score)

    def _upsert_decision(
        self,
        deduped: dict[str, CandidateDecision],
        alias_to_key: dict[str, str],
        decision: CandidateDecision,
    ) -> None:
        aliases = self._dedupe_aliases(decision.record)
        existing_key = next((alias_to_key[alias] for alias in aliases if alias in alias_to_key), None)
        key = existing_key or self._dedupe_key(decision.record)
        previous = deduped.get(key)
        if previous and self._rank(previous) >= self._rank(decision):
            self._merge_discovery_provenance(previous.record, decision.record)
            return
        if previous:
            self._merge_discovery_provenance(decision.record, previous.record)
        deduped[key] = decision
        for alias in aliases:
            alias_to_key[alias] = key

    def _merge_discovery_provenance(self, target: OpportunityRecord, incoming: OpportunityRecord) -> None:
        target_provenance = ensure_dict(target.discovery_provenance)
        incoming_provenance = ensure_dict(incoming.discovery_provenance)
        models = sorted(
            set(str(item) for item in ensure_list(target_provenance.get("discovered_by_models")) if item)
            | set(str(item) for item in ensure_list(incoming_provenance.get("discovered_by_models")) if item)
        )
        domains = sorted(
            set(str(item) for item in ensure_list(target_provenance.get("source_domains")) if item)
            | set(str(item) for item in ensure_list(incoming_provenance.get("source_domains")) if item)
        )
        query_count = max(
            ensure_int(target_provenance.get("discovery_query_count"), 0),
            ensure_int(incoming_provenance.get("discovery_query_count"), 0),
        )
        target_provenance["discovered_by_models"] = models
        target_provenance["source_domains"] = domains
        target_provenance["discovery_query_count"] = query_count
        target_provenance["consensus_strength"] = consensus_strength(models)
        target.discovery_provenance = target_provenance

    def _semantic_purity_failure(self, record: OpportunityRecord) -> str | None:
        text = self._semantic_text(record)
        blocked = self._matched_keywords(text, ABSOLUTE_NON_TECHNICAL_KEYWORDS)
        if blocked:
            return f"non_technical_creative_contest:{','.join(blocked)}"
        technical = self._matched_keywords(text, STRONG_TECHNICAL_SIGNALS)
        if not technical:
            return "missing_strong_technical_signal"
        if not self._has_implementation_signal(text):
            return "missing_technical_implementation_submission"
        return None

    def _deterministic_event_type(self, record: OpportunityRecord) -> tuple[str, str]:
        text = self._semantic_text(record)
        title_text = normalize_space(f"{record.hackathon_name or ''} {record.full_name or ''}".lower())
        invalid = self._matched_keywords(text, FORBIDDEN_OPPORTUNITY_KEYWORDS)
        competition = self._matched_keywords(text, COMPETITION_KEYWORDS + COMPETITIVE_STRUCTURE_SIGNALS)
        implementation = self._has_implementation_signal(text)
        hackathon_named = bool(self._matched_keywords(title_text, HACKATHON_TYPE_KEYWORDS))

        if invalid and not (hackathon_named and implementation):
            return "reject", f"forbidden_opportunity_type:{','.join(invalid[:4])}"
        if not competition and not hackathon_named:
            return "reject", "missing_competition_structure"
        if not implementation:
            return "reject", "missing_technical_implementation_submission"

        if self._matched_keywords(text, CYBERSECURITY_TYPE_KEYWORDS):
            if self._matched_keywords(text, ["ctf", "capture the flag"]):
                return "ctf", "deterministic_keyword_classifier"
            return "cybersecurity_competition", "deterministic_keyword_classifier"
        if self._matched_keywords(text, DEFENCE_TYPE_KEYWORDS):
            return "defence_innovation_challenge", "deterministic_keyword_classifier"
        if self._matched_keywords(text, AI_TYPE_KEYWORDS):
            return "ai_challenge", "deterministic_keyword_classifier"
        if hackathon_named:
            return "hackathon", "deterministic_keyword_classifier"
        if self._matched_keywords(text, CODING_TYPE_KEYWORDS):
            return "coding_competition", "deterministic_keyword_classifier"
        if self._matched_keywords(text, ["challenge", "competition", "contest", "grand challenge", "innovation challenge"]):
            return "technical_challenge", "deterministic_keyword_classifier"
        return "reject", "unclassified_opportunity_type"

    def _is_startup_seed_funding(self, record: OpportunityRecord) -> bool:
        text = self._semantic_text(record)
        if self._matched_keywords(text, ["startup", "startup india", "startup challenge", "startup program", "startup scheme"]):
            return True
        funding_terms = [
            "seed",
            "seed funding",
            "seed-stage",
            "pre-seed",
            "funding",
            "grant",
            "venture",
            "vc ",
            "angel",
            "investor",
        ]
        funding_signal = bool(self._matched_keywords(text, funding_terms))
        competition_signal = bool(self._matched_keywords(text, COMPETITION_KEYWORDS + COMPETITIVE_STRUCTURE_SIGNALS))
        implementation_signal = self._has_implementation_signal(text)
        return bool(funding_signal and not (competition_signal and implementation_signal))

    def _is_proposal_or_r_and_d_only(self, record: OpportunityRecord) -> bool:
        text = self._semantic_text(record)
        url_text = normalize_space(
            " ".join(
                str(url or "").lower()
                for url in [
                    record.source_url,
                    record.official_event_page,
                    record.registration_url,
                    record.submission_url,
                ]
            )
        )
        title_text = normalize_space(f"{record.hackathon_name or ''} {record.full_name or ''}".lower())
        combined_text = normalize_space(f"{text} {title_text} {url_text}")
        proposal_signal = bool(
            self._matched_keywords(
                combined_text,
                R_AND_D_PROPOSAL_ONLY_KEYWORDS + GRANT_ONLY_KEYWORDS + PROCUREMENT_OR_GRANT_INTAKE_KEYWORDS,
            )
        )
        hackathon_title_signal = "hackathon" in title_text or "coding competition" in title_text
        if hackathon_title_signal:
            return False
        # If the source explicitly marks the opportunity as a proposal call,
        # treat it as non-hackathon R&D/proposal intake.
        if record.current_status and "proposal" in str(record.current_status).lower():
            return True
        explicit_proposal_call = any(
            phrase in combined_text
            for phrase in (
                "call for proposal",
                "call for proposals",
                "callforproposals",
                "request for proposal",
                "request for proposals",
                "joint innovation call",
                "research proposal",
                "r&d",
                "research and development",
                "rdi fund",
                "rdif",
                "cfp",
                "howtosubmitproposal",
                "s&t cooperation call",
            )
        )
        return bool(proposal_signal and explicit_proposal_call)

    def _is_school_awareness_only(self, record: OpportunityRecord) -> bool:
        text = self._semantic_text(record)
        if "bioe3" not in text:
            return False
        has_school_category = (
            ("category 1" in text or "category-1" in text)
            and ("school" in text or "school student" in text or "school students" in text)
        )
        has_awareness_only_signal = bool(self._matched_keywords(text, SCHOOL_AWARENESS_ONLY_KEYWORDS)) and (
            "awareness" in text or "video submission" in text or "school" in text
        )
        return bool(has_school_category or has_awareness_only_signal)

    def _has_implementation_signal(self, text: str) -> bool:
        direct_implementation = self._matched_keywords(text, IMPLEMENTATION_SIGNALS)
        if direct_implementation:
            return True
        if self._matched_keywords(text, NON_IMPLEMENTATION_ONLY_KEYWORDS):
            return False
        contextual = self._matched_keywords(text, CONTEXTUAL_IMPLEMENTATION_TERMS)
        technical = self._matched_keywords(text, STRONG_TECHNICAL_SIGNALS)
        return bool(contextual and technical)

    def _semantic_text(self, record: OpportunityRecord) -> str:
        fields: list[Any] = [
            record.hackathon_name,
            record.full_name,
            record.event_type,
            record.domain,
            record.theme,
            record.format,
            record.mode,
            record.hosting_organization,
            record.ministry,
            record.platform,
            record.eligibility_criteria,
            record.prizes,
            record.funding_support,
            record.incubation_support,
            record.procurement_or_pilot_opportunity,
            record.focus_areas,
            record.subdomains,
            record.problem_statements,
            record.source_validation.model_dump(mode="json", exclude_none=True),
            record.tags,
        ]
        return normalize_space(json.dumps(fields, ensure_ascii=False, sort_keys=True).lower())

    def _matched_keywords(self, text: str, keywords: list[str]) -> list[str]:
        return [keyword for keyword in keywords if re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", text)]

    def _has_minimum_identity(self, record: OpportunityRecord) -> bool:
        return bool(record.hackathon_name and (record.registration_url or record.source_url or record.official_event_page))

    def _has_government_source(self, record: OpportunityRecord) -> bool:
        official_urls = [record.source_url, record.official_event_page, record.official_website]
        return any(is_trusted_government_url(url) for url in official_urls)

    def _has_government_or_ecosystem_affiliation(self, record: OpportunityRecord) -> bool:
        text = self._semantic_text(record)
        government_terms = [
            "government",
            "ministry",
            "department",
            "dpiit",
            "startup india",
            "indiaai",
            "mygov",
            "idex",
            "birac",
            "aicte",
            "meity",
            "drdo",
            "isro",
            "public sector",
            "psu",
            "state government",
        ]
        if any(term in text for term in government_terms):
            return True
        provenance_domains = ensure_list(record.discovery_provenance.get("source_domains"))
        return any(is_trusted_government_url(canonicalize_url(str(domain))) for domain in provenance_domains)

    def _has_current_cycle_evidence(self, record: OpportunityRecord) -> bool:
        if record.current_status in ACTIVE_STATUSES:
            return True
        if record.source_validation.official_open_keywords_found:
            return True
        deadline = parse_date(record.deadline)
        current = parse_date(self.current_date) or date.today()
        return bool(deadline and deadline >= current)

    def _uses_external_intake(self, record: OpportunityRecord) -> bool:
        return any(
            is_external_registration_url(url)
            for url in [
                record.registration_url,
                record.submission_url,
                record.source_validation.final_url,
            ]
        )

    def _is_ecosystem_relevant(self, record: OpportunityRecord) -> bool:
        text = self._semantic_text(record)
        ecosystem_terms = [
            "challenge",
            "grand challenge",
            "open innovation",
            "startup challenge",
            "innovation mission",
            "innovation program",
            "innovation programme",
            "technical call",
            "applied research",
            "hackathon",
            "competition",
            "pilot",
            "prototype",
            "solution",
        ]
        return any(term in text for term in ecosystem_terms)

    def _deadline_is_past(self, record: OpportunityRecord) -> bool:
        deadline = parse_date(record.deadline)
        current = parse_date(self.current_date) or date.today()
        return bool(deadline and deadline < current)

    def _procurement_or_grant_ambiguity(self, record: OpportunityRecord) -> str | None:
        text = self._semantic_text(record)
        grant_signal = bool(self._matched_keywords(text, GRANT_ONLY_KEYWORDS))
        procurement_signal = bool(self._matched_keywords(text, PROCUREMENT_OR_GRANT_INTAKE_KEYWORDS))
        hard_procurement_signal = bool(self._matched_keywords(text, HARD_PROCUREMENT_KEYWORDS))
        if not (grant_signal or procurement_signal):
            return None
        if hard_procurement_signal:
            return "procurement_or_feasibility_intake_semantics"
        if not self._has_primary_competition_structure(record):
            return "procurement_or_grant_intake_without_clear_competition_structure"
        return None

    def _has_primary_competition_structure(self, record: OpportunityRecord) -> bool:
        officialish_text = normalize_space(
            json.dumps(
                [
                    record.hackathon_name,
                    record.full_name,
                    record.theme,
                    record.problem_statements,
                    record.prizes,
                    record.source_validation.official_open_keywords_found,
                    record.borderline_reason,
                    record.exclusion_reason,
                ],
                ensure_ascii=False,
                sort_keys=True,
            ).lower()
        )
        competition_structure = self._matched_keywords(officialish_text, COMPETITIVE_STRUCTURE_SIGNALS)
        technical_depth = self._matched_keywords(officialish_text, TECHNICAL_EVALUATION_SIGNALS)
        return bool(competition_structure and (technical_depth or "challenge" in officialish_text))

    def _active_intake_issue(self, record: OpportunityRecord) -> str | None:
        validation = record.source_validation
        if not validation.registration_page_verified:
            return "registration_page_not_verified"
        if not validation.registration_form_detected:
            return "active_intake_workflow_not_confirmed"
        if validation.auth_wall_detected and not (
            validation.official_confirmation_found and validation.deadline_verified
        ):
            return "auth_wall_intake_not_externally_confirmed"
        return None

    def _dedupe_key(self, record: OpportunityRecord) -> str:
        # Prefer stable, meaningful keys in this order: canonical URL, title+deadline, title+org, normalized title
        aliases = sorted(self._dedupe_aliases(record))
        # Make a preference order for common alias prefixes
        preferred_prefixes = ["url:", "title_deadline:", "title_org:", "title:"]
        for prefix in preferred_prefixes:
            for a in aliases:
                if a.startswith(prefix):
                    return a
        return aliases[0] if aliases else f"title:{self._normalized_title(record.hackathon_name)}"

    def _dedupe_aliases(self, record: OpportunityRecord) -> set[str]:
        aliases: set[str] = set()
        title = self._normalized_title(record.hackathon_name or record.full_name or "")
        normalized_extra_title = self._normalized_title(str(record.deduplication.get("normalized_title", "")))
        deadline = record.deadline or record.application_deadline or record.submission_close_date or record.proposal_deadline or ""
        organizer = self._normalized_title(record.hosting_organization or record.ministry or "")
        theme = self._normalized_title(record.theme or record.domain or "")
        for value in {title, normalized_extra_title}:
            if value:
                aliases.add(f"title:{value}")
                if deadline:
                    aliases.add(f"title_deadline:{value}:{deadline}")
                # Add a simplified title alias that strips common category tokens
                simple = re.sub(r"category\s*\d+|category-\d+|school|open to all|india", "", value)
                simple = re.sub(r"\s+", " ", simple).strip()
                if simple:
                    aliases.add(f"title_simple:{simple}")
        if organizer and deadline and theme:
            aliases.add(f"org_deadline_theme:{organizer}:{deadline}:{theme}")
        if title and organizer:
            aliases.add(f"title_org:{title}:{organizer}")
        bioe3_alias = self._bioe3_category_alias(record)
        if bioe3_alias:
            aliases.add(bioe3_alias)
        known_alias = self._known_program_alias(record)
        if known_alias:
            aliases.add(known_alias)
        canonical = canonicalize_url(record.deduplication.get("canonical_url") or record.source_url or record.registration_url)
        if canonical:
            aliases.add(f"url:{canonical}")
        return aliases

    def _bioe3_category_alias(self, record: OpportunityRecord) -> str | None:
        text = self._semantic_text(record)
        if "bioe3" not in text:
            return None
        if (
            ("category 1" in text or "category-1" in text)
            and ("school" in text or "school student" in text or "school students" in text)
        ):
            return "bioe3:category1:school"
        if "category 2" in text or "category-2" in text or "open to all indian citizens" in text:
            return "bioe3:category2:open-to-all"
        return None

    def _known_program_alias(self, record: OpportunityRecord) -> str | None:
        text = self._semantic_text(record)
        title = self._normalized_title(record.hackathon_name or record.full_name or "")
        if "bharat startup grand challenge" in text or title in {
            "bharat startup",
            "bharat startup 2026",
            "bsgc 2026",
        }:
            return "program:bharat-startup-grand-challenge"
        return None

    def _normalized_title(self, value: str) -> str:
        value = value.lower()
        value = value.replace("&", " and ")
        value = re.sub(r"d\.?e\.?s\.?i\.?g\.?n\.?", "design", value)
        value = re.sub(r"bio[-\s]?ai", "bioai", value)
        value = re.sub(r"i\.?d\.?e\.?x\.?", "idex", value)
        value = re.sub(r"d\.?b\.?t\.?", "dbt", value)
        value = re.sub(r"b\.?i\.?r\.?a\.?c\.?", "birac", value)
        tokens = re.findall(r"[a-z0-9]+", value)
        tokens = [token for token in tokens if not re.fullmatch(r"20\d{2}", token)]
        tokens = [token for token in tokens if token not in TITLE_STOP_WORDS]
        return " ".join(tokens)

    def _rank(self, decision: CandidateDecision) -> tuple[int, int]:
        bucket_rank = RECALL_TIER_ORDER.get(decision.bucket, 0)
        return bucket_rank, decision.record.confidence_score

    def _dump_record(self, record: OpportunityRecord) -> dict[str, Any]:
        payload = record.model_dump(
            mode="json",
            exclude_none=True,
            exclude={
                "discovery_provenance",
                "search_metadata",
                "confidence_reasons",
                "deduplication",
                "funding_support",
                "incubation_support",
                "procurement_or_pilot_opportunity",
            },
        )
        payload["source_validation"] = record.source_validation.model_dump(mode="json", exclude_none=True)
        return payload

    def _dedupe_excluded(self, excluded: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        result: list[dict[str, Any]] = []
        for item in excluded:
            key = canonicalize_url(item.get("source_url")) or slugify(str(item.get("name") or item.get("hackathon_name") or ""))
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result


class OpportunityIntelligenceEngine:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.silent = bool(getattr(args, "silent", False))
        self.models = resolve_models(args)
        self.current_date = iso_date(args.current_date)
        if not self.current_date:
            raise SystemExit("--current-date must be parseable as a date.")
        self.checkpoints = CheckpointStore()
        self.extractor = JsonExtractor()
        self.planner = DiscoveryPlanner(self.current_date, args.max_candidates_per_task)
        self.normalizer = NormalizationEngine(
            self.current_date,
            min_confidence=args.min_confidence,
            live_validation=not args.skip_live_validation,
        )
        self.coverage_engine = CoverageIntelligenceEngine(COVERAGE_HISTORY_JSON)
        self.dashboard = TerminalDashboard(enabled=not self.silent and sys.stdout.isatty())
        if self.dashboard.enabled:
            self.dashboard.__enter__()

    def _tui(self, state: str, message: str) -> None:
        if self.silent:
            return
        if self.dashboard.enabled:
            self.dashboard.set_phase(state, message)
            return
        print(f"{state}... {message}")

    def _typing(self, state: str, message: str) -> TypingIndicator:
        if self.silent:
            return NullContext()
        if self.dashboard.enabled:
            return self.dashboard.stage(state, message)
        return TypingIndicator(state, message)

    def _runner_status(self, event: str, payload: dict[str, Any]) -> None:
        if self.silent or not self.dashboard.enabled:
            return
        task_name = str(payload.get("task_name") or "idle")
        model = str(payload.get("model") or self.dashboard._model_name)
        if event == "task_start":
            self.dashboard.set_task(task_name, "initializing")
            self.dashboard.note(f"starting {task_name}")
        elif event == "model_selected":
            self.dashboard.set_model(model, "selected")
        elif event == "model_start":
            self.dashboard.set_model(model, f"attempt {payload.get('attempt')} / timeout {payload.get('timeout')}s")
        elif event == "model_complete":
            self.dashboard.set_model(model, f"done success={payload.get('success')} elapsed={payload.get('elapsed', 0):.1f}s")
        elif event == "model_timeout":
            self.dashboard.set_model(model, f"timed out at {payload.get('timeout')}s")
        elif event == "cache_hit":
            self.dashboard.set_model(model, "cache hit")
        elif event == "retrying":
            self.dashboard.set_model(model, f"retrying in {payload.get('delay')}s")
        elif event == "fallback":
            self.dashboard.set_model(model, "fallback activated")
        elif event == "parser_rejected":
            self.dashboard.note(f"parser rejected {task_name}")
        elif event == "task_complete":
            self.dashboard.set_task(task_name, "completed" if payload.get("success") else "no usable output")
        elif event == "fatal_error":
            self.dashboard.note(f"fatal error on {task_name}")

    def run(self) -> dict[str, Any] | None:
        if self.args.validate_existing:
            with self._typing("thinking", f"validating existing JSON: {self.args.validate_existing}"):
                payload = read_json_file(Path(self.args.validate_existing))
            with self._typing("hashing", "normalizing payloads into final JSON"):
                final_payload = self.normalizer.normalize_payloads([payload])
            saturation_tracker = SearchSaturationTracker()
            queries = final_payload.get("metadata", {}).get("search_queries_used", [])
            saturation_tracker.record_query_batch(
                queries=ensure_list(queries),
                new_events_discovered=final_payload["metadata"]["total_active_hackathons"],
                duplicate_events_discovered=0,
                rejected_events_discovered=final_payload["metadata"]["total_excluded"],
            )
            final_payload["metadata"]["models_attempted"] = self.models
            if not TRIAGE_ONLY_MODE:
                self._attach_coverage_analysis(final_payload, [], [payload], [], saturation_tracker)
            with self._typing("thinking", "exporting validated JSON"):
                if self._export(final_payload):
                    if not TRIAGE_ONLY_MODE:
                        self.coverage_engine.persist_history(final_payload)
            return final_payload

        tasks = self.planner.tasks()
        if self.args.max_tasks is not None:
            tasks = tasks[: max(0, self.args.max_tasks)]

        if self.args.dry_run:
            if self.dashboard.enabled:
                self.dashboard.set_summary(
                    current_date=self.current_date,
                    models=f"{len(self.models)} models",
                    live_validation=str(not self.args.skip_live_validation),
                )
                self.dashboard.bump(tasks_total=len(tasks), tasks_done=0, tasks_failed=0, accepted=0, novelty=0, candidates=0, rejected=0)
                self.dashboard.note(f"dry run ready with {len(tasks)} tasks")
            else:
                self._print_plan(tasks)
            return None

        if not (self.silent or self.dashboard.enabled):
            print("╭─ Hackathon Intelligence TUI")
        self._tui("thinking", f"current_date: {self.current_date}")
        self._tui("hashing", f"models: {', '.join(self.models)}")
        self._tui("thinking", f"tasks queued: {len(tasks)}")
        self._tui("hashing", f"live_validation: {not self.args.skip_live_validation}")
        self._tui("thinking", "timeout_mode: model-specific")
        if self.args.timeout is not None or self.args.run_budget:
            self._tui("thinking", "--timeout/--run-budget are ignored; MODEL_TIMEOUTS controls execution")
        self._tui("completed", "run context loaded")

        runner = OpenCodeRunner(
            models=self.models,
            checkpoint_store=self.checkpoints,
            retries=self.args.retries,
            current_date=self.current_date,
            refresh_cache=self.args.refresh_cache,
            status_callback=self._runner_status,
            quiet=self.silent or self.dashboard.enabled,
        )

        payloads: list[dict[str, Any]] = []
        all_artifacts: list[RunArtifact] = []
        saturation_tracker = SearchSaturationTracker()
        low_novelty_streak = 0
        seen_keys: set[str] = set()

        parser_diagnostics: list[dict[str, Any]] = []
        self.dashboard.set_summary(
            current_date=self.current_date,
            models=f"{len(self.models)} models",
            live_validation=str(not self.args.skip_live_validation),
        )
        self.dashboard.bump(tasks_total=len(tasks), tasks_done=0, tasks_failed=0, accepted=0, novelty=0, candidates=0, rejected=0)

        for task in tasks:
            current_done = self.dashboard._counters["tasks_done"]
            with self._typing("thinking", f"{task.name}: building prompt"):
                prompt = self.planner.build_prompt(task)
            payload = None
            artifacts: list[RunArtifact] = []
            output: str | None = None

            def output_acceptor(artifact: RunArtifact) -> bool:
                parsed, _ = self.extractor.parse_payload(artifact.output)
                artifact.parser_diagnostics = dict(self.extractor.last_diagnostics)
                accepted = parsed is not None and self._payload_has_usable_candidates(parsed)
                artifact.parser_accepted = accepted
                self._tui(
                    "parsing",
                    f"{task.name}: accepted={accepted} strategy={artifact.parser_diagnostics.get('strategy')} salvaged={artifact.parser_diagnostics.get('salvaged_candidates', 0)} blocks={artifact.parser_diagnostics.get('candidate_blocks', 0)}",
                )
                return accepted

            for cache_retry in range(2):
                with self._typing("thinking", f"{task.name}: running scrape attempt {cache_retry + 1}"):
                    output, artifacts = runner.run_task(task, prompt, output_acceptor=output_acceptor)
                all_artifacts.extend(artifacts)
                if not output:
                    break
                with self._typing("parsing", f"{task.name}: parsing model output"):
                    payload, warnings = self.extractor.parse_payload(output)
                diagnostics = dict(self.extractor.last_diagnostics)
                diagnostics["task_name"] = task.name
                parser_diagnostics.append(diagnostics)
                for warning in warnings:
                    self._tui("warning", f"{task.name}: {warning}")
                if payload:
                    self._tui("completed", f"{task.name}: candidates accepted")
                    break
                if any(artifact.from_cache for artifact in artifacts):
                    self._tui("thinking", f"{task.name}: deleting unusable cached output and rerunning")
                    self.checkpoints.delete_success(task)
                    continue
                break
            if not payload:
                self._tui("warning", f"{task.name}: no usable candidates")
                saturation_tracker.record_query_batch(
                    queries=task.queries,
                    new_events_discovered=0,
                    duplicate_events_discovered=0,
                    rejected_events_discovered=0,
                )
                self.dashboard.bump(tasks_done=current_done + 1, tasks_failed=self.dashboard._counters["tasks_failed"] + 1)
                continue
            cacheable_artifact = next(
                (
                    artifact
                    for artifact in artifacts
                    if artifact.success and not artifact.from_cache and artifact.output == output
                ),
                None,
            )
            if cacheable_artifact and output:
                self.checkpoints.save_success(task, self.current_date, cacheable_artifact.model, output)
            payload.setdefault("metadata", {})
            payload["metadata"]["task_name"] = task.name
            payload["metadata"]["task_category"] = task.category
            payload["metadata"]["discovery_model"] = self._accepted_model_for_output(artifacts, output)
            payloads.append(payload)

            with self._typing("hashing", f"{task.name}: scoring discovery yield"):
                novelty = self._estimate_novelty(payload, seen_keys)
                total_candidates = self._payload_candidate_count(payload)
                rejected_candidates = len(ensure_list(payload.get("excluded"))) + len(ensure_list(payload.get("excluded_opportunities")))
            self._tui("hashing", f"{task.name}: novelty={novelty} candidates={total_candidates} rejected={rejected_candidates}")
            saturation_tracker.record_query_batch(
                queries=task.queries,
                new_events_discovered=novelty,
                duplicate_events_discovered=max(0, total_candidates - novelty),
                rejected_events_discovered=rejected_candidates,
            )
            self.dashboard.bump(
                tasks_done=current_done + 1,
                accepted=self.dashboard._counters["accepted"] + 1,
                novelty=self.dashboard._counters["novelty"] + novelty,
                candidates=self.dashboard._counters["candidates"] + total_candidates,
                rejected=self.dashboard._counters["rejected"] + rejected_candidates,
            )
            low_novelty_streak = low_novelty_streak + 1 if novelty <= 1 else 0
            if self.args.stop_on_saturation and len(payloads) >= 3 and low_novelty_streak >= 2:
                self._tui("completed", "stopping early due to low novelty saturation")
                break

        with self._typing("hashing", "normalizing payloads into final JSON"):
            final_payload = self.normalizer.normalize_payloads(payloads)
        final_payload["metadata"]["models_attempted"] = self.models
        final_payload["metadata"]["discovery_tasks_requested"] = [task.name for task in tasks[:MAX_STORED_DIAGNOSTICS]]
        final_payload["metadata"]["checkpoint_run_dir"] = str(self.checkpoints.run_dir)
        final_payload["metadata"]["parser_summary"] = {
            "tasks_parsed": len(parser_diagnostics),
            "strategies": sorted(
                {
                    str(item.get("strategy"))
                    for item in parser_diagnostics
                    if isinstance(item, dict) and item.get("strategy")
                }
            ),
            "salvaged_candidates": sum(
                ensure_int(item.get("salvaged_candidates"), 0)
                for item in parser_diagnostics[:MAX_STORED_DIAGNOSTICS]
                if isinstance(item, dict)
            ),
        }
        final_payload["metadata"]["opencode_artifact_summary"] = {
            "attempts": len(all_artifacts),
            "successful_attempts": sum(1 for artifact in all_artifacts if artifact.success),
            "cache_hits": sum(1 for artifact in all_artifacts if artifact.from_cache),
            "timeouts": sum(1 for artifact in all_artifacts if artifact.timed_out),
            "failed_tasks": sorted({artifact.task_name for artifact in all_artifacts if not artifact.success}),
        }
        if not TRIAGE_ONLY_MODE:
            self._attach_coverage_analysis(final_payload, tasks, payloads, all_artifacts, saturation_tracker)
        with self._typing("thinking", "exporting final JSON"):
            if self._export(final_payload):
                if not TRIAGE_ONLY_MODE:
                    self.coverage_engine.persist_history(final_payload)
        self.checkpoints.save_run_summary(final_payload["metadata"])
        self._tui("completed", "export finished")
        return final_payload

    def _payload_has_usable_candidates(self, payload: dict[str, Any]) -> bool:
        for key in ("government_hackathons", "candidates", "opportunities", "active_opportunities"):
            if any(isinstance(item, dict) for item in ensure_list(payload.get(key))):
                return True
        return False

    def _payload_candidate_count(self, payload: dict[str, Any]) -> int:
        total = 0
        for key in ("government_hackathons", "candidates", "opportunities", "active_opportunities"):
            total += len([item for item in ensure_list(payload.get(key)) if isinstance(item, dict)])
        return total

    def _accepted_model_for_output(self, artifacts: list[RunArtifact], output: str | None) -> str | None:
        accepted = next(
            (
                artifact
                for artifact in artifacts
                if artifact.success and artifact.parser_accepted is not False and artifact.output == output
            ),
            None,
        )
        return accepted.model if accepted else None

    def _attach_coverage_analysis(
        self,
        final_payload: dict[str, Any],
        tasks: list[DiscoveryTask],
        payloads: list[dict[str, Any]],
        artifacts: list[RunArtifact],
        saturation_tracker: SearchSaturationTracker,
    ) -> None:
        coverage_analysis = self.coverage_engine.analyze(
            final_payload=final_payload,
            discovery_tasks=tasks,
            payloads=payloads,
            artifacts=artifacts,
            saturation_tracker=saturation_tracker,
            models_attempted=self.models,
        )
        metadata = final_payload.setdefault("metadata", {})
        metadata["coverage_analysis"] = coverage_analysis
        metadata["coverage_confidence"] = coverage_analysis["coverage_confidence"]
        metadata["coverage_status"] = coverage_analysis["coverage_status"]

    def _estimate_novelty(self, payload: dict[str, Any], seen_keys: set[str]) -> int:
        novelty = 0
        for item in ensure_list(payload.get("candidates")) + ensure_list(payload.get("government_hackathons")):
            if not isinstance(item, dict):
                continue
            key = canonicalize_url(
                item.get("source_url")
                or item.get("official_event_page")
                or item.get("registration_url")
                or item.get("official_website")
            ) or slugify(str(item.get("hackathon_name") or item.get("full_name") or ""))
            if key not in seen_keys:
                seen_keys.add(key)
                novelty += 1
        return novelty

    def _print_plan(self, tasks: list[DiscoveryTask]) -> None:
        if self.silent:
            return
        print("Discovery plan")
        print(f"  current_date: {self.current_date}")
        print(f"  models: {', '.join(self.models)}")
        print(f"  live_validation: {not self.args.skip_live_validation}")
        for index, task in enumerate(tasks, 1):
            print(f"\n{index}. {task.name} [{task.category}] priority={task.priority}")
            print("   seeds:")
            for url in task.seed_urls:
                print(f"   - {url}")
            print("   queries:")
            for query in task.queries:
                print(f"   - {query}")

    def _export(self, payload: dict[str, Any]) -> bool:
        output_path = Path(self.args.output)
        is_empty_discovery = (
            payload["metadata"]["total_active_hackathons"] == 0
            and payload["metadata"]["total_excluded"] == 0
        )
        if (
            is_empty_discovery
            and output_path.exists()
            and not self.args.allow_empty_output
            and not self.args.validate_existing
        ):
            if not self.silent:
                print("\ncompleted... skipped overwriting existing JSON because this discovery run produced no usable candidates.")
                print(f"  preserved: {output_path}")
            try:
                existing = read_json_file(output_path)
                metadata = existing.get("metadata", {}) if isinstance(existing, dict) else {}
                if not self.silent:
                    print(f"  existing_active: {metadata.get('total_active_hackathons', 'unknown')}")
            except (OSError, json.JSONDecodeError):
                pass
            if not self.silent:
                print("  use --allow-empty-output to replace it with an empty result")
            return False
        atomic_write_json(output_path, payload)
        self._write_final_cleared(output_path)
        if not self.silent:
            print("\ncompleted... exported deterministic JSON")
            print(f"  path: {output_path}")
            print(f"  active: {payload['metadata']['total_active_hackathons']}")
            print(f"  excluded: {payload['metadata']['total_excluded']}")
        return True

    def _write_final_cleared(self, source_path: Path) -> None:
        final_path = PROJECT_DIR / "hackathons_final_cleared.json"
        try:
            result = build_final_cleared_file(source_path, final_path)
        except (OSError, json.JSONDecodeError, FileNotFoundError) as exc:
            if not self.silent:
                print(f"  final_clear: skipped ({exc})")
            return

        stats = result["stats"]
        if not self.silent:
            print(f"  final_clear: {final_path}")
            print(f"    kept: {stats.kept_records}")
            print(f"    removed_proposal_rnd: {stats.removed_proposal_rnd}")
            print(f"    removed_startup: {stats.removed_startup}")
            print(f"    removed_duplicate: {stats.removed_duplicate}")


def main() -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    args = parse_args()
    engine = OpportunityIntelligenceEngine(args)
    try:
        engine.run()
    finally:
        if getattr(engine, "dashboard", None) and engine.dashboard.enabled:
            engine.dashboard.__exit__(None, None, None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
