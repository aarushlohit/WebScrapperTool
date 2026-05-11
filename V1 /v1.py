"""Iterative Indian Government Hackathon Intelligence Engine (V1).

This module keeps the old codebase untouched and runs a multi-round AI-assisted
reconnaissance pipeline entirely within the V1 folder.

Key properties:
- AI is used only for reconnaissance and semantic discovery.
- Deterministic Python code performs filtering, deduplication, and validation.
- Each round writes a self-contained JSON artifact and updates cache files.
- The pipeline stops on novelty, duplicate, or runtime thresholds.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlparse, urlunparse

try:
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover - optional dependency
    BeautifulSoup = None

try:
    import httpx
except Exception:  # pragma: no cover - optional dependency
    httpx = None

try:
    from rapidfuzz import fuzz
except Exception:  # pragma: no cover - optional dependency
    fuzz = None

try:
    from pydantic import BaseModel, Field
except Exception:  # pragma: no cover - optional dependency
    BaseModel = object  # type: ignore[assignment]
    Field = None  # type: ignore[assignment]

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich.progress import Progress, BarColumn, DownloadColumn, TextColumn, timeremaining
    from rich.layout import Layout
    from rich.live import Live
    from rich.columns import Columns
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

import threading
from collections import deque
import itertools


ROOT_DIR = Path(__file__).resolve().parent
LOGS_DIR = ROOT_DIR / "logs"
CACHE_DIR = ROOT_DIR / "cache"
OUTPUT_DIR = ROOT_DIR / "output"
OUTPUT_ARCHIVE_DIR = OUTPUT_DIR / "archive"
SYSTEMPROMPT_PATH = ROOT_DIR / "systemprompt.md"
MODEL_DEFAULT = os.environ.get("V1_OPENCODE_MODEL", "opencode/minimax-m2.5-free")
NEXT_MODEL_DEFAULT = os.environ.get("V1_OPENCODE_NEXT_MODEL", "github-copilot/gpt-5-mini")
AVAILABLE_MODELS = [
    "opencode/minimax-m2.5-free",
    "github-copilot/gpt-5-mini",
    "cloudflare-workers-ai/@cf/moonshotai/kimi-k2.6",
    "opencode/ring-2.6-1t-free",
    "opencode/nemotron-3-super-free",
    "opencode/big-pickle",
    "google/gemini-3-pro-preview",
]
MAX_ROUNDS_DEFAULT = 3
ROUND_TIMEOUT_DEFAULT = 340
RUNTIME_BUDGET_SECONDS = 8 * 60
NOVELTY_STOP_THRESHOLD = 0.05
DUPLICATE_STOP_THRESHOLD = 0.80
CONSECUTIVE_EMPTY_THRESHOLD = 2

ROUND1_OUTPUT = OUTPUT_DIR / "round_1_candidates.json"
MASTER_OUTPUT = OUTPUT_DIR / "master_candidates.json"
FINAL_OUTPUT = OUTPUT_DIR / "final_export.json"
MEGA_FINAL_OUTPUT = OUTPUT_DIR / "mega_final_export.json"
READY4DB_OUTPUT = OUTPUT_DIR / "archive" / "hackathon_ready4db.json"
ROOT_ROUND_SUMMARIES = [
    ROOT_DIR / "round_1_summary.json",
    ROOT_DIR / "round_2_summary.json",
    ROOT_DIR / "round_3_summary.json",
]
ROOT_RUN_ARTIFACTS = [
    ROOT_DIR / "raw_candidates.json",
]
CACHE_URLS = CACHE_DIR / "known_urls.json"
CACHE_TITLES = CACHE_DIR / "known_titles.json"
CACHE_DOMAINS = CACHE_DIR / "known_domains.json"
CACHE_HASHES = CACHE_DIR / "known_hashes.json"
CACHE_DOMAIN_SKIP = CACHE_DIR / "scanned_domains.json"
CACHE_URL_SKIP = CACHE_DIR / "scanned_urls.json"
CACHE_CONTENT_HASH = CACHE_DIR / "seen_hashes.json"
CACHE_MINISTRIES = CACHE_DIR / "known_ministries.json"
CACHE_STATE = CACHE_DIR / "known_state.json"
ROUND_LOG_PREFIX = "round_"

DOMAIN_PRIORITY_HIGH = {
    "gov.in", "nic.in", "mygov.in", "indiaai.gov.in", "idex.gov.in",
    "data.gov.in", "mod.gov.in", "drdo.gov.in", "isro.gov.in", "dot.gov.in", "meity.gov.in",
}

DOMAIN_PRIORITY_MEDIUM = {
    "iit.ac.in", "iiit.in", "nfsu.ac.in", "stpi.in",
}

DOMAIN_PRIORITY_LOW = {
    "medium.com", "linkedin.com", "blogger.com", "wordpress.com",
}

ALLOWED_EVENT_TYPES = {
    "hackathon",
    "innovation challenge",
    "ai challenge",
    "cybersecurity competition",
    "coding competition",
    "defence innovation competition",
    "technical government competition",
    "ctf competition",
    "hackathon competition",
    "technical challenge",
    "govtech challenge",
    "public-sector technology competition",
}

FORBIDDEN_KEYWORDS = [
    "incubation",
    "accelerator",
    "seed funding",
    "cohort",
    "call for proposal",
    "proposal",
    "grant",
    "rfp",
    "tender",
    "venture",
    "investment",
    "funding support",
    "startup",
    "startup program",
    "fellowship",
    "internship",
    "webinar",
    "conference",
]

CLOSED_KEYWORDS = [
    "winners announced",
    "registration closed",
    "applications closed",
    "archived",
    "completed",
    "concluded",
    "results announced",
    "submission closed",
]

OPEN_KEYWORDS = [
    "registration open",
    "applications open",
    "submission open",
    "apply now",
    "ongoing",
    "open for registration",
    "open now",
]

GOVERNMENT_KEYWORDS = [
    "gov.in",
    "nic.in",
    "mygov",
    "india.gov.in",
    "pib.gov.in",
    "meity",
    "ministry",
    "department",
    "isro",
    "drdo",
    "cdac",
    "idex",
    "indiaai",
    "startupindia",
    "smart city",
]

DEFAULT_SEED_DOMAINS = [
    "mygov.in",
    "gov.in",
    "nic.in",
    "india.gov.in",
    "pib.gov.in",
    "indiaai.gov.in",
    "idex.gov.in",
    "drdo.gov.in",
    "isro.gov.in",
    "cdac.in",
    "meity.gov.in",
    "startupindia.gov.in",
    "innovateindia.mygov.in",
    "sih.gov.in",
    "nciipc.gov.in",
    "stpi.in",
    "digitalindia.gov.in",
]

DEFAULT_SEED_QUERY_HINTS = [
    "registration open",
    "applications open",
    "submission open",
    "hackathon",
    "innovation challenge",
    "AI challenge",
    "cybersecurity competition",
    "coding competition",
    "defence innovation challenge",
    "CTF",
]




class SessionLogger:
    """Session logger for real-time debugging."""
    def __init__(self, session_dir):
        self.session_dir = Path(session_dir)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_file = self.session_dir / f"session_{self.session_id}.log"
        self.events = deque(maxlen=100)
        self.lock = threading.Lock()
        self._write_header()
    
    def _write_header(self):
        header = f"\n{'='*70}\nSession: {self.session_id}\nStart: {datetime.now()}\n{'='*70}\n"
        with open(self.session_file, "w") as f:
            f.write(header)
    
    def log(self, level, message, context=None):
        """Log message with timestamp."""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        log_entry = f"[{timestamp}] [{level:<6}] {message}"
        if context:
            log_entry += f" | {context}"
        
        with self.lock:
            self.events.append(log_entry)
        
        try:
            with open(self.session_file, "a") as f:
                f.write(log_entry + "\n")
        except Exception:
            pass
    
    def get_session_file(self):
        return str(self.session_file)


class DynamicProgressUI:
    """Enhanced real-time terminal UI with working animations."""
    def __init__(self, logger, max_rounds=3):
        self.logger = logger
        self.console = Console() if RICH_AVAILABLE else None
        self.start_time = time.time()
        self.current_round = 0
        self.max_rounds = max_rounds
        self.total_candidates = 0
        self.status_message = "Initializing..."
        self.spinner_frames = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
        self.spinner_idx = 0
        self.round_metrics = {"novel": 0, "duplicates": 0, "rejected": 0}
        self.lock = threading.Lock()
        self.running = True
        
        # Start background animation thread
        self.anim_thread = threading.Thread(target=self._animate_loop, daemon=True)
        self.anim_thread.start()
    
    def _animate_loop(self):
        """Background thread that animates the UI continuously."""
        while self.running:
            try:
                with self.lock:
                    self._render_line()
                time.sleep(0.1)  # Smooth animation ~10fps
            except Exception:
                pass
    
    def _render_line(self):
        """Render a single line with current metrics."""
        try:
            elapsed = time.time() - self.start_time
            minutes, seconds = divmod(int(elapsed), 60)
            time_str = f"{minutes:02d}:{seconds:02d}"
            
            # Spinner frame
            spinner = self.spinner_frames[self.spinner_idx % len(self.spinner_frames)]
            self.spinner_idx += 1
            
            # Progress bar
            round_progress = self.current_round / max(1, self.max_rounds)
            bar_length = 15
            filled = int(round_progress * bar_length)
            bar = "█" * filled + "░" * (bar_length - filled)
            progress_pct = int(round_progress * 100)
            
            # Build output line
            line = (
                f"\r{spinner} Round {self.current_round}/{self.max_rounds} {bar} {progress_pct:3d}% | "
                f"✓{self.round_metrics['novel']:2d} "
                f"~{self.round_metrics['duplicates']:2d} "
                f"✗{self.round_metrics['rejected']:2d} | "
                f"Total:{self.total_candidates:3d} | "
                f"{self.status_message[:40]:<40} | "
                f"{time_str}"
            )
            
            # Write directly to stdout
            sys.stdout.write(line)
            sys.stdout.flush()
            
        except Exception:
            pass
    
    def update(self, round_num, total, status, novel=0, duplicates=0, rejected=0):
        """Update display metrics."""
        with self.lock:
            self.current_round = round_num
            self.total_candidates = total
            self.status_message = status
            self.round_metrics = {"novel": novel, "duplicates": duplicates, "rejected": rejected}
    
    def render(self):
        """Manual render (called by background thread)."""
        pass
    
    def final_summary(self, total_time, total_candidates=None):
        """Print final summary and stop animation."""
        self.running = False  # Stop background thread
        time.sleep(0.2)  # Let thread finish
        
        if total_candidates is not None:
            self.total_candidates = total_candidates
        
        print()  # New line
        print("\n" + "="*75)
        print("✅ PIPELINE COMPLETE - SUCCESS!", flush=True)
        print("="*75)
        
        minutes, seconds = divmod(int(total_time), 60)
        print(f"🎯 Total Candidates Found:  {self.total_candidates}")
        print(f"⏱️  Total Runtime:          {minutes:02d}:{seconds:02d}")
        print(f"📝 Session Log:             {self.logger.get_session_file()}")
        print()
        print("✨ All data exported successfully!")
        print("="*75)
        print()


# Keep old name as alias for compatibility
RealtimeProgressUI = DynamicProgressUI


@dataclass(slots=True)
class CandidateRecord:
    event_type: str = ""
    hackathon_name: str = ""
    full_name: str = ""
    current_status: str = "unknown"
    registration_url: str = ""
    submission_url: str = ""
    official_website: str = ""
    official_event_page: str = ""
    source_url: str = ""
    hosting_organization: str = ""
    ministry: str = ""
    institution_type: str = ""
    platform: str = ""
    domain: str = ""
    theme: str = ""
    sdg_alignment: list[str] = field(default_factory=list)
    focus_areas: list[str] = field(default_factory=list)
    problem_statements: list[str] = field(default_factory=list)
    deadline: str = ""
    registration_close_date: str = ""
    submission_close_date: str = ""
    application_deadline: str = ""
    eligibility_criteria: dict[str, Any] = field(default_factory=lambda: {"summary": ""})
    team_size: dict[str, Any] = field(default_factory=lambda: {"minimum": None, "maximum": None})
    submission_fee: dict[str, Any] = field(default_factory=lambda: {"amount": None, "currency": "INR", "display": ""})
    prizes: dict[str, Any] = field(default_factory=lambda: {"summary": ""})
    tags: list[str] = field(default_factory=list)
    source_validation: dict[str, Any] = field(default_factory=lambda: {
        "source_type": "",
        "official_confirmation_found": False,
        "official_open_keywords_found": [],
        "deadline_verified": False,
    })
    source_text: str = ""

    def canonical_title(self) -> str:
        title = self.hackathon_name or self.full_name or self.theme or self.official_event_page or self.source_url
        return normalize_space(title)

    def canonical_domain(self) -> str:
        if self.domain:
            return normalize_domain(self.domain)
        for value in (self.registration_url, self.official_event_page, self.official_website, self.source_url):
            if value:
                parsed = urlparse(value)
                if parsed.netloc:
                    return normalize_domain(parsed.netloc)
        return ""

    def duplicate_key(self) -> str:
        parts = [
            normalize_space(self.canonical_title()).lower(),
            normalize_url(self.registration_url or self.official_event_page or self.source_url),
            normalize_url(self.official_event_page),
            normalize_domain(self.canonical_domain()),
            normalize_space(self.theme).lower(),
        ]
        return stable_hash("|".join(part for part in parts if part))


@dataclass(slots=True)
class RoundSummary:
    round_number: int
    novel_candidates: list[dict[str, Any]] = field(default_factory=list)
    duplicates_skipped: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    sources_scanned: list[str] = field(default_factory=list)
    coverage_gaps: list[str] = field(default_factory=list)
    new_domains_discovered: list[str] = field(default_factory=list)
    runtime_seconds: float = 0.0


@dataclass(slots=True)
class EngineState:
    known_urls: set[str] = field(default_factory=set)
    known_titles: set[str] = field(default_factory=set)
    known_domains: set[str] = field(default_factory=set)
    known_hashes: set[str] = field(default_factory=set)
    known_ministries: set[str] = field(default_factory=set)
    ministry_coverage: dict[str, int] = field(default_factory=dict)
    master_candidates: list[CandidateRecord] = field(default_factory=list)
    excluded_opportunities: list[dict[str, Any]] = field(default_factory=list)
    scanned_sources: list[str] = field(default_factory=list)


class CandidateSchema(BaseModel):  # type: ignore[misc]
    event_type: str = ""
    hackathon_name: str = ""
    full_name: str = ""
    current_status: str = "unknown"
    registration_url: str = ""
    submission_url: str = ""
    official_website: str = ""
    official_event_page: str = ""
    source_url: str = ""
    hosting_organization: str = ""
    ministry: str = ""
    institution_type: str = ""
    platform: str = ""
    domain: str = ""
    theme: str = ""
    sdg_alignment: list[str] = []
    focus_areas: list[str] = []
    problem_statements: list[str] = []
    deadline: str = ""
    registration_close_date: str = ""
    submission_close_date: str = ""
    application_deadline: str = ""
    eligibility_criteria: dict[str, Any] = {"summary": ""}
    team_size: dict[str, Any] = {"minimum": None, "maximum": None}
    submission_fee: dict[str, Any] = {"amount": None, "currency": "INR", "display": ""}
    prizes: dict[str, Any] = {"summary": ""}
    tags: list[str] = []
    source_validation: dict[str, Any] = {
        "source_type": "",
        "official_confirmation_found": False,
        "official_open_keywords_found": [],
        "deadline_verified": False,
    }


class IterativeHackathonEngine:
    def __init__(
        self,
        model: str = MODEL_DEFAULT,
        max_rounds: int = MAX_ROUNDS_DEFAULT,
        round_timeout: int = ROUND_TIMEOUT_DEFAULT,
        runtime_budget_seconds: int = RUNTIME_BUDGET_SECONDS,
        current_date: date | None = None,
    ) -> None:
        self.model = model
        self.session_logger = SessionLogger(LOGS_DIR)
        self.ui = DynamicProgressUI(self.session_logger, max_rounds=max_rounds)
        self.session_logger.log("INFO", "Engine initialized", f"model={model}")
        self.max_rounds = max_rounds
        self.round_timeout = round_timeout
        self.runtime_budget_seconds = runtime_budget_seconds
        self.current_date = current_date or date.today()
        self.root_dir = ROOT_DIR
        self.systemprompt = SYSTEMPROMPT_PATH.read_text(encoding="utf-8")
        self.state = EngineState()
        self.run_started_at = time.time()
        self.consecutive_empty_rounds = 0
        self.round_metrics = {}
        self._scanned_domains = set()
        self._scanned_urls = set()
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._load_state()

    def run(self, output_path: Path = FINAL_OUTPUT) -> dict[str, Any]:
        self.session_logger.log("INFO", "Pipeline started", f"max_rounds={self.max_rounds}")
        round_summaries: list[RoundSummary] = []
        for round_number in range(1, self.max_rounds + 1):
            if self._runtime_budget_exceeded():
                break

            round_started = time.time()
            self.ui.update(round_number, 0, "🔍 Running reconnaissance...", novel=0, duplicates=0, rejected=0)
            raw_round = self._run_single_round(round_number)
            self.session_logger.log("INFO", "Round completed", f"round={round_number}, raw_items={len(raw_round.get('novel_candidates', []))}")
            
            self.ui.update(round_number, 0, "⚙️  Processing results...", novel=0, duplicates=0, rejected=0)
            summary = self._process_round(round_number, raw_round, round_started)
            round_summaries.append(summary)

            total_found = sum(len(s.novel_candidates) for s in round_summaries)
            self.ui.update(
                round_number, 
                total_found, 
                f"✅ Found {len(summary.novel_candidates)} novel candidates",
                novel=len(summary.novel_candidates),
                duplicates=len(summary.duplicates_skipped),
                rejected=len(summary.rejected)
            )
            self.session_logger.log("INFO", "Round processed", f"round={round_number}, novel={len(summary.novel_candidates)}, duplicates={len(summary.duplicates_skipped)}")

            self._persist_state(round_number, summary)

            if self._should_stop(summary):
                self.session_logger.log("INFO", "Stopping criteria met", f"stop_reason=novelty_threshold")
                break

        final_export = self._build_final_export(round_summaries)
        self._write_json(output_path, final_export)
        
        total_time = time.time() - self.ui.start_time
        total_candidates = len(final_export.get("government_hackathons", []))
        self.ui.final_summary(total_time, total_candidates)
        self.session_logger.log("INFO", "Pipeline completed", f"total_candidates={total_candidates}, runtime={total_time:.1f}s")
        print()  # Final newline
        return final_export

    def _run_single_round(self, round_number: int) -> dict[str, Any]:
        self.session_logger.log("DEBUG", "Starting reconnaissance", f"round={round_number}")
        prompt = self._build_round_prompt(round_number)
        raw_output = self._run_opencode(prompt, round_number)
        self._log_raw_output(round_number, prompt, raw_output)
        payload = parse_jsonish(raw_output)
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("round_number", round_number)
        payload.setdefault("duplicates_skipped", [])
        payload.setdefault("rejected", [])
        payload.setdefault("sources_scanned", [])
        payload.setdefault("coverage_gaps", [])
        payload.setdefault("new_domains_discovered", [])
        payload.setdefault("runtime_seconds", 0.0)
        novel_candidates = payload.get("novel_candidates") or payload.get("candidates") or []
        payload["novel_candidates"] = listify(novel_candidates)
        if "candidates" in payload:
            del payload["candidates"]
        self._log_parsed_output(round_number, payload)
        return payload

    def _process_round(self, round_number: int, payload: dict[str, Any], round_started: float) -> RoundSummary:
        summary = RoundSummary(round_number=round_number)
        summary.sources_scanned = listify(payload.get("sources_scanned"))
        summary.coverage_gaps = listify(payload.get("coverage_gaps"))
        summary.new_domains_discovered = listify(payload.get("new_domains_discovered"))

        all_items = listify(payload.get("novel_candidates"))
        early_dupes = [item for item in all_items if self._early_dedupe(item)]
        summary.duplicates_skipped.extend(early_dupes)
        candidates_for_validation = [item for item in all_items if item not in early_dupes]
        novel = [self._coerce_candidate(item) for item in candidates_for_validation]
        duplicates = listify(payload.get("duplicates_skipped"))
        rejected = listify(payload.get("rejected"))

        for candidate in novel:
            accepted, reason = self._accept_candidate(candidate)
            if accepted:
                self._store_candidate(candidate)
                summary.novel_candidates.append(candidate_to_dict(candidate))
            else:
                rejected_entry = candidate_to_dict(candidate)
                rejected_entry["reason"] = reason
                summary.rejected.append(rejected_entry)
                self._record_exclusion(candidate, reason)

        summary.duplicates_skipped.extend(duplicates)
        for item in rejected:
            self.state.excluded_opportunities.append(item if isinstance(item, dict) else {"reason": "rejected", "item": item})

        summary.runtime_seconds = max(0.0, time.time() - round_started)
        if round_number == 1:
            self._write_json(ROUND1_OUTPUT, {
                "round_number": round_number,
                "novel_candidates": summary.novel_candidates,
                "duplicates_skipped": summary.duplicates_skipped,
                "rejected": summary.rejected,
                "sources_scanned": summary.sources_scanned,
                "coverage_gaps": summary.coverage_gaps,
                "new_domains_discovered": summary.new_domains_discovered,
                "runtime_seconds": summary.runtime_seconds,
            })

        self._write_json(self.root_dir / f"{ROUND_LOG_PREFIX}{round_number}_summary.json", {
            "round_number": round_number,
            "novel_candidates": summary.novel_candidates,
            "duplicates_skipped": summary.duplicates_skipped,
            "rejected": summary.rejected,
            "sources_scanned": summary.sources_scanned,
            "coverage_gaps": summary.coverage_gaps,
            "new_domains_discovered": summary.new_domains_discovered,
            "runtime_seconds": summary.runtime_seconds,
        })
        return summary

    def _should_stop(self, summary: RoundSummary) -> bool:
        if summary.novel_candidates:
            self.consecutive_empty_rounds = 0
        else:
            self.consecutive_empty_rounds += 1
        total_seen = len(self.state.master_candidates) + len(summary.novel_candidates)
        if total_seen == 0 and summary.round_number == 1:
            return False
        novelty_rate = len(summary.novel_candidates) / max(1, total_seen) if total_seen > 0 else 0
        duplicate_ratio = len(summary.duplicates_skipped) / max(1, len(summary.duplicates_skipped) + len(summary.novel_candidates)) if (len(summary.duplicates_skipped) + len(summary.novel_candidates)) > 0 else 0
        if summary.round_number >= self.max_rounds:
            return True
        if self.consecutive_empty_rounds >= CONSECUTIVE_EMPTY_THRESHOLD and summary.round_number > 1:
            return True
        if novelty_rate < NOVELTY_STOP_THRESHOLD and self.consecutive_empty_rounds >= CONSECUTIVE_EMPTY_THRESHOLD:
            return True
        if duplicate_ratio > DUPLICATE_STOP_THRESHOLD and not summary.novel_candidates:
            return True
        return False

    def _build_round_prompt(self, round_number: int) -> str:
        state_snapshot = self._state_snapshot()
        if round_number == 1:
            round_focus = (
                "Round 1: broad reconnaissance discovery. Discover only currently active Indian government-backed "
                "hackathons, innovation challenges, AI challenges, coding competitions, cybersecurity competitions, "
                "CTF competitions, defence innovation competitions, and govtech technical competitions."
            )
        else:
            uncovered = ", ".join(state_snapshot["coverage_gaps"][:12]) or "missed ministries, niche portals, and newly opened competitions"
            round_focus = (
                f"Round {round_number}: difference-focused rediscovery. Focus on uncovered areas such as: {uncovered}."
            )

        compact_snapshot = {
            "known_urls_count": state_snapshot["known_urls_count"],
            "known_titles_count": state_snapshot["known_titles_count"],
            "known_domains_count": state_snapshot["known_domains_count"],
            "key_domains": sorted(state_snapshot["known_domains"])[:10],
            "key_ministries": sorted(state_snapshot["known_ministries"])[:10],
            "coverage_gaps": state_snapshot["coverage_gaps"],
            "seed_priority_domains": list(DOMAIN_PRIORITY_HIGH)[:15],
        }

        prompt = f"""{self.systemprompt.strip()}

Current date: {self.current_date.isoformat()}
Round number: {round_number}

Mission:
- execute iterative discovery
- maximize recall without hallucinating
- return JSON only
- do not repeat already known URLs/titles/domains
- focus on active, open competitions only

Known state (compressed):
{json.dumps(compact_snapshot, indent=2, ensure_ascii=False)}

{round_focus}

Return JSON with this shape exactly:
{{
  "round_number": {round_number},
  "novel_candidates": [],
  "duplicates_skipped": [],
  "rejected": [],
  "sources_scanned": [],
  "coverage_gaps": [],
  "new_domains_discovered": [],
  "runtime_seconds": 0
}}

Candidate fields if you include any candidate:
- event_type
- hackathon_name
- full_name
- current_status
- registration_url
- submission_url
- official_website
- official_event_page
- source_url
- hosting_organization
- ministry
- institution_type
- platform
- domain
- theme
- sdg_alignment
- focus_areas
- problem_statements
- deadline
- registration_close_date
- submission_close_date
- application_deadline
- eligibility_criteria
- team_size
- submission_fee
- prizes
- tags
- source_validation

Hard requirement:
- output valid JSON only
"""
        return prompt

    def _run_opencode(self, prompt: str, round_number: int = 0) -> str:
        round_start = time.time()
        process = subprocess.Popen(
            ["opencode", "run", "-m", self.model, prompt],
            cwd=self.root_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            text=True,
        )
        try:
            output, _ = process.communicate(timeout=self.round_timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate()
        if round_number:
            self.round_metrics[f"round_{round_number}_ai_runtime"] = round(time.time() - round_start, 2)
        return output or ""

    def _coerce_candidate(self, item: Any) -> CandidateRecord:
        if isinstance(item, CandidateRecord):
            return item
        if isinstance(item, dict):
            data = {
                "event_type": str(item.get("event_type", "")).strip(),
                "hackathon_name": str(item.get("hackathon_name", "")).strip(),
                "full_name": str(item.get("full_name", item.get("hackathon_name", ""))).strip(),
                "current_status": str(item.get("current_status", "unknown")).strip() or "unknown",
                "registration_url": str(item.get("registration_url", "")).strip(),
                "submission_url": str(item.get("submission_url", "")).strip(),
                "official_website": str(item.get("official_website", "")).strip(),
                "official_event_page": str(item.get("official_event_page", "")).strip(),
                "source_url": str(item.get("source_url", "")).strip(),
                "hosting_organization": str(item.get("hosting_organization", "")).strip(),
                "ministry": str(item.get("ministry", "")).strip(),
                "institution_type": str(item.get("institution_type", "")).strip(),
                "platform": str(item.get("platform", "")).strip(),
                "domain": str(item.get("domain", "")).strip(),
                "theme": str(item.get("theme", "")).strip(),
                "sdg_alignment": listify(item.get("sdg_alignment")),
                "focus_areas": listify(item.get("focus_areas")),
                "problem_statements": listify(item.get("problem_statements")),
                "deadline": str(item.get("deadline", "")).strip(),
                "registration_close_date": str(item.get("registration_close_date", "")).strip(),
                "submission_close_date": str(item.get("submission_close_date", "")).strip(),
                "application_deadline": str(item.get("application_deadline", "")).strip(),
                "eligibility_criteria": item.get("eligibility_criteria") or {"summary": ""},
                "team_size": item.get("team_size") or {"minimum": None, "maximum": None},
                "submission_fee": item.get("submission_fee") or {"amount": None, "currency": "INR", "display": ""},
                "prizes": item.get("prizes") or {"summary": ""},
                "tags": listify(item.get("tags")),
                "source_validation": item.get("source_validation") or {
                    "source_type": "",
                    "official_confirmation_found": False,
                    "official_open_keywords_found": [],
                    "deadline_verified": False,
                },
                "source_text": str(item.get("source_text", "")).strip(),
            }
            return CandidateRecord(**data)
        return CandidateRecord()

    def _accept_candidate(self, candidate: CandidateRecord) -> tuple[bool, str]:
        normalized = normalize_candidate(candidate, self.current_date)
        rejection_reason = hard_negative_reason(normalized)
        if rejection_reason:
            return False, rejection_reason

        dedupe_reason = self._dedupe_reason(normalized)
        if dedupe_reason:
            return False, dedupe_reason

        if not is_active_candidate(normalized, self.current_date):
            return False, "closed"

        return True, "accepted"

    def _dedupe_reason(self, candidate: CandidateRecord) -> str:
        url = normalize_url(candidate.registration_url or candidate.official_event_page or candidate.source_url)
        title = normalize_space(candidate.canonical_title()).lower()
        title_hash = stable_hash(title)
        candidate_hash = candidate.duplicate_key()

        if url and url in self.state.known_urls:
            return "duplicate"
        if title and title in self.state.known_titles:
            return "duplicate"
        if candidate_hash in self.state.known_hashes:
            return "duplicate"

        if fuzz is not None:
            for known_title in self.state.known_titles:
                if known_title and fuzz.ratio(title, known_title) >= 96:
                    return "duplicate"

        if candidate_hash:
            self.state.known_hashes.add(candidate_hash)
        if title:
            self.state.known_titles.add(title)
        if url:
            self.state.known_urls.add(url)
        domain = candidate.canonical_domain()
        if domain:
            self.state.known_domains.add(domain)
        return ""

    def _store_candidate(self, candidate: CandidateRecord) -> None:
        self.state.master_candidates.append(candidate)
        domain = candidate.canonical_domain()
        title = normalize_space(candidate.canonical_title()).lower()
        url = normalize_url(candidate.registration_url or candidate.official_event_page or candidate.source_url)
        self.state.known_domains.add(domain)
        self.state.known_titles.add(title)
        if url:
            self.state.known_urls.add(url)
        self.state.known_ministries.add(normalize_space(candidate.ministry).lower())
        if candidate.ministry:
            self.state.ministry_coverage[candidate.ministry] = self.state.ministry_coverage.get(candidate.ministry, 0) + 1

    def _record_exclusion(self, candidate: CandidateRecord, reason: str) -> None:
        self.state.excluded_opportunities.append({
            "name": candidate.canonical_title(),
            "source_url": candidate.source_url or candidate.registration_url or candidate.official_event_page,
            "reason": reason,
        })

    def _persist_state(self, round_number: int, summary: RoundSummary) -> None:
        self._write_json(MASTER_OUTPUT, {
            "government_hackathons": [candidate_to_dict(candidate) for candidate in self.state.master_candidates],
            "excluded_opportunities": self.state.excluded_opportunities,
            "metadata": self._metadata(round_number, summary),
        })
        self._write_json(CACHE_URLS, sorted(self.state.known_urls))
        self._write_json(CACHE_TITLES, sorted(self.state.known_titles))
        self._write_json(CACHE_DOMAINS, sorted(self.state.known_domains))
        self._write_json(CACHE_HASHES, sorted(self.state.known_hashes))
        self._write_json(CACHE_DOMAIN_SKIP, sorted(self._scanned_domains))
        self._write_json(CACHE_URL_SKIP, sorted(self._scanned_urls))
        self._write_json(CACHE_MINISTRIES, sorted(self.state.known_ministries))
        self._write_json(CACHE_STATE, {
            "ministry_coverage": self.state.ministry_coverage,
            "master_candidates": len(self.state.master_candidates),
            "excluded_opportunities": len(self.state.excluded_opportunities),
            "scanned_sources": self.state.scanned_sources,
            "round_metrics": self.round_metrics,
        })

    def _metadata(self, round_number: int, summary: RoundSummary) -> dict[str, Any]:
        runtime = time.time() - self.run_started_at
        return {
            "rounds_completed": round_number,
            "total_candidates_discovered": len(self.state.master_candidates) + len(self.state.excluded_opportunities),
            "novel_candidates_retained": len(self.state.master_candidates),
            "duplicates_removed": len(self.state.known_hashes),
            "runtime_seconds": round(runtime, 2),
            "coverage_confidence": round(self._coverage_confidence(), 3),
            "per_round_metrics": self.round_metrics,
            "early_dedupe_count": len(self._scanned_urls),
        }

    def _build_final_export(self, round_summaries: list[RoundSummary]) -> dict[str, Any]:
        runtime = time.time() - self.run_started_at
        return {
            "government_hackathons": [candidate_to_dict(candidate) for candidate in self.state.master_candidates],
            "excluded_opportunities": self.state.excluded_opportunities,
            "metadata": {
                "rounds_completed": len(round_summaries),
                "total_candidates_discovered": len(self.state.master_candidates) + len(self.state.excluded_opportunities),
                "novel_candidates_retained": len(self.state.master_candidates),
                "duplicates_removed": len(self.state.known_hashes),
                "runtime_seconds": round(runtime, 2),
                "coverage_confidence": round(self._coverage_confidence(), 3),
                "round_summaries": [asdict(summary) for summary in round_summaries],
            },
        }

    def _coverage_confidence(self) -> float:
        if not self.state.master_candidates:
            return 0.0
        covered_domains = len({candidate.canonical_domain() for candidate in self.state.master_candidates if candidate.canonical_domain()})
        return min(1.0, covered_domains / max(1, len(DEFAULT_SEED_DOMAINS)))

    def _load_state(self) -> None:
        self.state.known_urls = set(load_json(CACHE_URLS, default=[]))
        self.state.known_titles = set(load_json(CACHE_TITLES, default=[]))
        self.state.known_domains = set(load_json(CACHE_DOMAINS, default=[]))
        self.state.known_hashes = set(load_json(CACHE_HASHES, default=[]))
        self.state.known_ministries = set(load_json(CACHE_MINISTRIES, default=[]))
        self._scanned_domains = set(load_json(CACHE_DOMAIN_SKIP, default=[]))
        self._scanned_urls = set(load_json(CACHE_URL_SKIP, default=[]))
        master_payload = load_json(MASTER_OUTPUT, default={})
        if isinstance(master_payload, dict):
            previous_candidates = master_payload.get("government_hackathons", [])
            if isinstance(previous_candidates, list):
                self.state.master_candidates = [self._coerce_candidate(item) for item in previous_candidates]
            previous_excluded = master_payload.get("excluded_opportunities", [])
            if isinstance(previous_excluded, list):
                self.state.excluded_opportunities = [item for item in previous_excluded if isinstance(item, dict)]
        state_payload = load_json(CACHE_STATE, default={})
        if isinstance(state_payload, dict):
            coverage = state_payload.get("ministry_coverage", {})
            if isinstance(coverage, dict):
                self.state.ministry_coverage = {str(k): int(v) for k, v in coverage.items()}
            scanned = state_payload.get("scanned_sources", [])
            if isinstance(scanned, list):
                self.state.scanned_sources = [str(item) for item in scanned]

    def _state_snapshot(self) -> dict[str, Any]:
        coverage_gaps = uncovered_coverage_gaps(self.state)
        return {
            "current_date": self.current_date.isoformat(),
            "known_urls_count": len(self.state.known_urls),
            "known_titles_count": len(self.state.known_titles),
            "known_domains_count": len(self.state.known_domains),
            "known_ministries": sorted(self.state.known_ministries),
            "known_domains": sorted(self.state.known_domains),
            "coverage_gaps": coverage_gaps,
            "seed_domains": DEFAULT_SEED_DOMAINS,
            "seed_query_hints": DEFAULT_SEED_QUERY_HINTS,
        }

    def _runtime_budget_exceeded(self) -> bool:
        return (time.time() - self.run_started_at) > self.runtime_budget_seconds

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, default=default_json)
        tmp.replace(path)


    def _log_raw_output(self, round_number: int, prompt: str, output: str) -> None:
        (LOGS_DIR / f"round_{round_number}_prompt.txt").write_text(prompt, encoding="utf-8")
        (LOGS_DIR / f"round_{round_number}_raw.txt").write_text(output, encoding="utf-8")

    def _log_parsed_output(self, round_number: int, payload: dict[str, Any]) -> None:
        (LOGS_DIR / f"round_{round_number}_parsed.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=default_json),
            encoding="utf-8"
        )

    def _early_dedupe(self, item: dict[str, Any]) -> bool:
        """Check if item is duplicate before expensive validation. Returns True if duplicate."""
        try:
            title = normalize_space(item.get("hackathon_name") or item.get("full_name") or "").lower()
            source_url = normalize_url(item.get("registration_url") or item.get("official_event_page") or item.get("source_url") or "")
            domain = normalize_domain(item.get("domain") or parse_qsl(urlparse(source_url).query if source_url else "")[0][0] or "")
            
            if title and title in self.state.known_titles:
                return True
            if source_url and source_url in self.state.known_urls:
                return True
            if domain and domain in self.state.known_domains:
                temp_url = source_url or ""
                if temp_url and temp_url in self.state.known_urls:
                    return True
            return False
        except Exception:
            return False

    def _get_domain_priority(self, domain: str) -> int:
        norm_domain = normalize_domain(domain)
        if any(norm_domain.endswith(d) for d in DOMAIN_PRIORITY_HIGH):
            return 3
        if any(norm_domain.endswith(d) for d in DOMAIN_PRIORITY_MEDIUM):
            return 2
        if any(norm_domain.endswith(d) for d in DOMAIN_PRIORITY_LOW):
            return 0
        return 1

def hard_negative_reason(candidate: CandidateRecord) -> str:
    text = " ".join(
        [
            candidate.event_type,
            candidate.hackathon_name,
            candidate.full_name,
            candidate.theme,
            candidate.hosting_organization,
            candidate.ministry,
            candidate.source_text,
        ]
    ).lower()

    if not any(keyword in text for keyword in ["hackathon", "challenge", "competition", "ctf", "innovation"]):
        return "no_active_workflow"

    for keyword in FORBIDDEN_KEYWORDS:
        if keyword.lower() in text:
            if any(token in text for token in ["incubat", "accelerat"]):
                return "incubation_program"
            if any(token in text for token in ["grant", "funding", "investment", "venture"]):
                if "government" not in text and "hackathon" not in text:
                    return "grant_program"
            if any(token in text for token in ["proposal", "rfp", "tender"]):
                return "proposal_or_rnd"
            if "startup" in keyword.lower():
                if any(t in text for t in ["msme", "innovator", "entrepreneur", "initiative", "scheme"]) and                    any(t in text for t in ["hackathon", "challenge", "competition"]):
                    continue
                if not any(t in text for t in ["hackathon", "challenge", "competition", "technical"]):
                    return "startup_focused"
    return ""


def is_active_candidate(candidate: CandidateRecord, current_date: date) -> bool:
    text = " ".join(
        [
            candidate.current_status,
            candidate.deadline,
            candidate.registration_close_date,
            candidate.submission_close_date,
            candidate.application_deadline,
            candidate.source_text,
        ]
    ).lower()
    if any(keyword in text for keyword in CLOSED_KEYWORDS):
        return False
    if any(keyword in text for keyword in OPEN_KEYWORDS):
        return True

    parsed_deadline = first_valid_date([
        candidate.registration_close_date,
        candidate.submission_close_date,
        candidate.application_deadline,
        candidate.deadline,
    ])
    if parsed_deadline is not None and parsed_deadline < current_date:
        return False
    return candidate.current_status.lower() in {"open", "active", "ongoing", "registration_open", "application_open", "submission_open"}


def normalize_candidate(candidate: CandidateRecord, current_date: date) -> CandidateRecord:
    candidate.event_type = normalize_space(candidate.event_type)
    candidate.hackathon_name = normalize_space(candidate.hackathon_name)
    candidate.full_name = normalize_space(candidate.full_name)
    candidate.current_status = normalize_space(candidate.current_status) or "unknown"
    candidate.registration_url = normalize_url(candidate.registration_url)
    candidate.submission_url = normalize_url(candidate.submission_url)
    candidate.official_website = normalize_url(candidate.official_website)
    candidate.official_event_page = normalize_url(candidate.official_event_page)
    candidate.source_url = normalize_url(candidate.source_url)
    candidate.hosting_organization = normalize_space(candidate.hosting_organization)
    candidate.ministry = normalize_space(candidate.ministry)
    candidate.institution_type = normalize_space(candidate.institution_type)
    candidate.platform = normalize_space(candidate.platform)
    candidate.domain = normalize_domain(candidate.domain)
    candidate.theme = normalize_space(candidate.theme)
    candidate.sdg_alignment = unique_list(candidate.sdg_alignment)
    candidate.focus_areas = unique_list(candidate.focus_areas)
    candidate.problem_statements = unique_list(candidate.problem_statements)
    candidate.tags = unique_list(candidate.tags)

    if not candidate.current_status or candidate.current_status == "unknown":
        if is_active_candidate(candidate, current_date):
            candidate.current_status = "open"
        else:
            candidate.current_status = "closed"

    if candidate.hackathon_name and not candidate.full_name:
        candidate.full_name = candidate.hackathon_name
    if not candidate.event_type:
        candidate.event_type = infer_event_type(candidate)
    return candidate


def infer_event_type(candidate: CandidateRecord) -> str:
    text = " ".join([candidate.hackathon_name, candidate.full_name, candidate.theme, candidate.source_text]).lower()
    if "ctf" in text:
        return "ctf competition"
    if "cyber" in text:
        return "cybersecurity competition"
    if "ai" in text or "machine learning" in text:
        return "ai challenge"
    if "defence" in text or "defense" in text:
        return "defence innovation competition"
    if "coding" in text or "code" in text:
        return "coding competition"
    if "innovation" in text:
        return "innovation challenge"
    return "technical government competition"


def uncovered_coverage_gaps(state: EngineState) -> list[str]:
    gaps: list[str] = []
    covered = {normalize_domain(domain) for domain in state.known_domains if domain}
    for domain in DEFAULT_SEED_DOMAINS:
        if normalize_domain(domain) not in covered:
            gaps.append(domain)
    if not state.known_ministries:
        gaps.append("missed ministries")
    else:
        known = {item.lower() for item in state.known_ministries}
        for ministry in ["meity", "isro", "drdo", "idex", "indiaai", "smart city"]:
            if ministry not in " ".join(known):
                gaps.append(ministry)
    return gaps


def parse_jsonish(text: str) -> Any:
    if not text:
        return {}
    text = text.replace("“", '"').replace("”", '"').replace("’", "'")
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    for block in fenced:
        parsed = safe_json_loads(block)
        if parsed is not None:
            return parsed
    parsed = safe_json_loads(text)
    if parsed is not None:
        return parsed
    brace_block = extract_balanced_json(text)
    if brace_block:
        parsed = safe_json_loads(brace_block)
        if parsed is not None:
            return parsed
    return {}


def safe_json_loads(text: str) -> Any | None:
    try:
        return json.loads(text)
    except Exception:
        repaired = repair_jsonish(text)
        try:
            return json.loads(repaired)
        except Exception:
            return None


def repair_jsonish(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
    cleaned = cleaned.replace("\u201c", '"').replace("\u201d", '"').replace("\u2019", "'")
    return cleaned


def extract_balanced_json(text: str) -> str:
    start = text.find("{")
    if start == -1:
        start = text.find("[")
    if start == -1:
        return ""
    depth = 0
    in_string = False
    escape = False
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    for index in range(start, len(text)):
        char = text[index]
        if escape:
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return ""


def normalize_space(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_domain(value: Any) -> str:
    domain = normalize_space(value).lower()
    domain = domain.replace("https://", "").replace("http://", "")
    domain = domain.split("/")[0]
    return domain


def normalize_url(value: Any) -> str:
    raw = normalize_space(value)
    if not raw:
        return ""
    parsed = urlparse(raw)
    if not parsed.scheme:
        raw = f"https://{raw.lstrip('/')}"
        parsed = urlparse(raw)
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if not k.lower().startswith("utm_")]
    cleaned = parsed._replace(fragment="", query="&".join([f"{k}={v}" if v else k for k, v in query]))
    path = cleaned.path.rstrip("/") if cleaned.path not in {"", "/"} else cleaned.path
    cleaned = cleaned._replace(path=path)
    return urlunparse(cleaned)


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def unique_list(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values or []:
        item = normalize_space(value)
        key = item.lower()
        if item and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def listify(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    if value == "":
        return []
    return [value]


def candidate_to_dict(candidate: CandidateRecord) -> dict[str, Any]:
    return asdict(candidate)


def default_json(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, set):
        return sorted(value)
    if hasattr(value, "dict"):
        return value.dict()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def archive_and_reset_outputs() -> Path | None:
    """Archive the current mega export and remove stale output artifacts."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    archive_target: Path | None = None
    if MEGA_FINAL_OUTPUT.exists():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_target = OUTPUT_ARCHIVE_DIR / f"{timestamp}_{MEGA_FINAL_OUTPUT.name}"
        shutil.move(str(MEGA_FINAL_OUTPUT), str(archive_target))

    for item in list(OUTPUT_DIR.iterdir()):
        if item == OUTPUT_ARCHIVE_DIR:
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()

    for path in ROOT_ROUND_SUMMARIES + ROOT_RUN_ARTIFACTS:
        if path.exists():
            path.unlink()

    return archive_target


def next_versioned_json_path(base_path: Path) -> Path:
    """Return next available path using stem_N suffix to avoid overwrites."""
    parent = base_path.parent
    stem = base_path.stem
    suffix = base_path.suffix or ".json"
    parent.mkdir(parents=True, exist_ok=True)
    index = 1
    while True:
        candidate = parent / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def first_valid_date(values: Iterable[Any]) -> date | None:
    for value in values:
        parsed = parse_date(value)
        if parsed is not None:
            return parsed
    return None


def parse_date(value: Any) -> date | None:
    text = normalize_space(value)
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(text[:11] if fmt == "%d %b %Y" else text, fmt).date()
        except Exception:
            pass
    iso_match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if iso_match:
        try:
            return datetime.fromisoformat(iso_match.group(0)).date()
        except Exception:
            return None
    return None


def maybe_fetch_page_metadata(urls: list[str], timeout: float = 15.0) -> list[dict[str, Any]]:
    if httpx is None:
        return []
    return asyncio.run(_maybe_fetch_page_metadata_async(urls, timeout))


async def _maybe_fetch_page_metadata_async(urls: list[str], timeout: float = 15.0) -> list[dict[str, Any]]:
    if httpx is None:
        return []
    results: list[dict[str, Any]] = []
    limits = httpx.Limits(max_connections=8, max_keepalive_connections=4)
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout, limits=limits, headers={"User-Agent": "Mozilla/5.0"}) as client:
        for url in urls:
            try:
                response = await client.get(url)
                text = response.text
                title = ""
                if BeautifulSoup is not None:
                    soup = BeautifulSoup(text, "html.parser")
                    title = normalize_space(soup.title.text if soup.title and soup.title.text else "")
                results.append({"url": url, "status_code": response.status_code, "title": title, "final_url": str(response.url)})
            except Exception:
                continue
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run iterative Indian government hackathon intelligence rounds")
    parser.add_argument("--model", default=MODEL_DEFAULT, choices=AVAILABLE_MODELS, help="LLM model to use")
    parser.add_argument("--nextmodel", default=NEXT_MODEL_DEFAULT, choices=AVAILABLE_MODELS, help="Second-pass model after first pass completes")
    parser.add_argument("--rounds", type=int, default=MAX_ROUNDS_DEFAULT, help="Maximum rounds to execute")
    parser.add_argument("--timeout", type=int, default=ROUND_TIMEOUT_DEFAULT, help="Timeout per round in seconds")
    parser.add_argument("--budget", type=int, default=RUNTIME_BUDGET_SECONDS, help="Runtime budget in seconds")
    parser.add_argument("--date", default=None, help="Current date override in YYYY-MM-DD format")
    parser.add_argument("--output", default=str(FINAL_OUTPUT), help="Final output file path")
    parser.add_argument("--clear", action="store_true", help="Clear old logs and cache before running")
    args = parser.parse_args(argv)

    # Clear logs, cache, and stale output artifacts if requested
    if args.clear:
        for directory in [LOGS_DIR, CACHE_DIR]:
            if directory.exists():
                shutil.rmtree(directory)
                print(f"✓ Cleared {directory}")
            directory.mkdir(parents=True, exist_ok=True)

        archived_run = archive_and_reset_outputs()
        if archived_run is not None:
            print(f"✓ Archived mega export to {archived_run}")
        else:
            print("✓ Reset output directory")

    current_date = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today()
    first_output = next_versioned_json_path(Path(args.output))
    engine = IterativeHackathonEngine(
        model=args.model,
        max_rounds=args.rounds,
        round_timeout=args.timeout,
        runtime_budget_seconds=args.budget,
        current_date=current_date,
    )
    final_export = engine.run(output_path=first_output)
    print(f"Saved first-pass export to: {first_output}")

    mega_engine = IterativeHackathonEngine(
        model=args.nextmodel,
        max_rounds=args.rounds,
        round_timeout=args.timeout,
        runtime_budget_seconds=args.budget,
        current_date=current_date,
    )
    mega_export = mega_engine.run(output_path=MEGA_FINAL_OUTPUT)
    print(f"Saved second-pass merged export to: {MEGA_FINAL_OUTPUT}")

    from centralized_government_hackathon_intelligence_layer import merge_and_write_ready4db

    ready4db = merge_and_write_ready4db(
        portal_output_path=first_output,
        mega_output_path=MEGA_FINAL_OUTPUT,
        ready4db_path=READY4DB_OUTPUT,
    )
    print(f"Saved archive-ready merged export to: {READY4DB_OUTPUT}")

    if MEGA_FINAL_OUTPUT.exists():
        MEGA_FINAL_OUTPUT.unlink()
    archive_and_reset_outputs()

    print(json.dumps(final_export, ensure_ascii=False, indent=2, default=default_json))
    print(json.dumps({
        "first_pass_output": str(first_output),
        "second_pass_output": str(MEGA_FINAL_OUTPUT),
        "ready4db_output": str(READY4DB_OUTPUT),
        "second_pass_model": args.nextmodel,
        "mega_total_candidates": len(mega_export.get("government_hackathons", [])),
        "ready4db_total_candidates": len(ready4db.get("government_hackathons", [])),
    }, ensure_ascii=False, indent=2, default=default_json))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
