"""Centralized Government Hackathon Portal Intelligence Layer.

This module implements a Playwright-first discovery layer for Indian government
hackathon ecosystems. It prioritizes centralized aggregators and official
ministry portals, extracts dynamic challenge cards, validates active/open
signals, and emits canonical candidate JSON.

The design intentionally favors browser automation over static scraping because
many of these portals render challenge data client-side.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

try:
    from playwright.async_api import async_playwright, Browser, BrowserContext, Page
except Exception:  # pragma: no cover - Playwright is optional in some environments.
    async_playwright = None
    Browser = Any  # type: ignore[assignment]
    BrowserContext = Any  # type: ignore[assignment]
    Page = Any  # type: ignore[assignment]


ROOT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT_DIR / "output"
OUTPUT_ARCHIVE_DIR = OUTPUT_DIR / "archive"
DEFAULT_OUTPUT = OUTPUT_DIR / "centralized_portal_intelligence.json"
DEFAULT_EXCLUDED = OUTPUT_DIR / "excluded_opportunities.json"
DEFAULT_CACHE = ROOT_DIR / "cache" / "centralized_portal_cache.json"
DEFAULT_MERGED_INPUT = OUTPUT_DIR / "mega_final_export.json"
DEFAULT_READY4DB = OUTPUT_ARCHIVE_DIR / "hackathon_ready4db.json"


@dataclass(frozen=True)
class PortalDefinition:
    name: str
    root_url: str
    discovery_paths: tuple[str, ...]
    priority: int
    is_aggregator: bool = False


@dataclass
class ChallengeCandidate:
    event_type: str = ""
    hackathon_name: str = ""
    full_name: str = ""
    current_status: str = ""
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
    eligibility_criteria: dict[str, Any] = field(default_factory=dict)
    team_size: dict[str, Any] = field(default_factory=dict)
    submission_fee: dict[str, Any] = field(default_factory=dict)
    prizes: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    source_validation: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


MANDATORY_PORTALS: tuple[PortalDefinition, ...] = (
    PortalDefinition("Innovate India (MyGov)", "https://innovateindia.mygov.in", ("/search-initiatives/",), 1, True),
    PortalDefinition("Open Government Data", "https://event.data.gov.in", ("/all-challenges/",), 1, True),
    PortalDefinition("AI Kosh", "https://aikosh.indiaai.gov.in", ("/",), 1, True),
    PortalDefinition("Defence India Startup Challenge", "https://idex.gov.in", ("/disc-category", "/challenges"), 1, True),
    PortalDefinition("Smart India Hackathon", "https://sih.gov.in", ("/sih{currentyear}PS",), 2, False),
    PortalDefinition("Bhashini", "https://bhashini.gov.in", ("/hackathon",), 2, False),
    PortalDefinition("MoE Innovation Cell", "https://gc.mic.gov.in", ("/",), 2, False),
    PortalDefinition("MeitY Startup Hub", "https://msh.meity.gov.in", ("/challenges",), 2, False),
    PortalDefinition("Geological Survey of India", "https://hackathon.gsi.gov.in", ("/",), 2, False),
    PortalDefinition("National Highways Authority of India", "https://nhai.gov.in", ("/hackathon",), 2, False),
    PortalDefinition("I4C Cybersecurity", "https://i4c.mha.gov.in", ("/hackathon",), 2, False),
    PortalDefinition("Startup India Challenges", "https://startupindia.gov.in", ("/innovation-challenges",), 2, False),
    PortalDefinition("RBI Innovation Hub", "https://rbih.org", ("/hackathons",), 2, False),
    PortalDefinition("ISRO Bhuvan", "https://bhuvan.nrsc.gov.in", ("/hackathon",), 2, False),
    PortalDefinition("Agri India", "https://agriindia.gov.in", ("/hackathon",), 2, False),
    PortalDefinition("Grand Challenges India", "https://grandchallengesindia.org", ("/challenges",), 2, False),
    PortalDefinition("CDAC Challenges", "https://cdac.in", ("/index.aspx?id=hackathons",), 2, False),
    PortalDefinition("NIC Cloud Hackathon", "https://cloud.nic.in", ("/hackathon",), 2, False),
    PortalDefinition("NPCI Hackathons", "https://www.npci.org.in", ("/hackathons",), 2, False),
    PortalDefinition("Ministry of Jal Shakti", "https://jalshakti-dowr.gov.in", ("/hackathon",), 2, False),
    PortalDefinition("Indian Railways Hackathon", "https://indianrailways.gov.in", ("/hackathon",), 2, False),
)


ACTIVE_HINTS = (
    "open",
    "active",
    "register",
    "apply",
    "ongoing",
    "live",
    "submission",
    "deadline",
    "challenge",
    "hackathon",
)

NORMALIZED_TAGS = (
    "innovation challenge",
    "hackathon",
    "government",
    "official portal",
    "central aggregator",
)
CLOSED_HINTS = (
    "closed",
    "archived",
    "results",
    "completed",
    "expired",
    "ended",
    "not open",
)


class CacheStore:
    def __init__(self, path: Path = DEFAULT_CACHE) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"seen_urls": [], "seen_titles": [], "rejected_urls": [], "metadata": {}}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            return loaded if isinstance(loaded, dict) else {}
        except Exception:
            return {}

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    @property
    def seen_urls(self) -> set[str]:
        return set(self.data.setdefault("seen_urls", []))

    @property
    def seen_titles(self) -> set[str]:
        return set(self.data.setdefault("seen_titles", []))

    @property
    def rejected_urls(self) -> set[str]:
        return set(self.data.setdefault("rejected_urls", []))

    def remember_url(self, url: str) -> None:
        seen = set(self.data.setdefault("seen_urls", []))
        if url and url not in seen:
            seen.add(url)
            self.data["seen_urls"] = sorted(seen)

    def remember_title(self, title: str) -> None:
        seen = set(self.data.setdefault("seen_titles", []))
        normalized = normalize_space(title).lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            self.data["seen_titles"] = sorted(seen)

    def remember_rejected(self, url: str) -> None:
        rejected = set(self.data.setdefault("rejected_urls", []))
        if url and url not in rejected:
            rejected.add(url)
            self.data["rejected_urls"] = sorted(rejected)


class CentralizedGovernmentHackathonIntelligenceLayer:
    def __init__(
        self,
        headless: bool = True,
        concurrency: int = 4,
        timeout_ms: int = 15000,
        cache: CacheStore | None = None,
    ) -> None:
        self.headless = headless
        self.concurrency = max(1, concurrency)
        self.timeout_ms = timeout_ms
        self.cache = cache or CacheStore()

    async def run(self) -> dict[str, Any]:
        if async_playwright is None:
            raise RuntimeError("playwright is required for dynamic portal extraction")

        candidates: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=self.headless)
            context = await browser.new_context(viewport={"width": 1440, "height": 1200})
            try:
                for portal in self._prioritized_portals():
                    portal_candidates, portal_excluded = await self._scrape_portal(context, portal)
                    candidates.extend(portal_candidates)
                    excluded.extend(portal_excluded)
            finally:
                await context.close()
                await browser.close()

        unique_candidates = self._dedupe_candidates(candidates)
        return {
            "generated_at": datetime.now().isoformat(),
            "portal_priority_order": [portal.name for portal in self._prioritized_portals()],
            "government_hackathons": unique_candidates,
            "excluded_opportunities": excluded,
            "recursive_discovery_prompt": self.build_recursive_discovery_prompt(unique_candidates, excluded),
        }

    def build_ready4db(
        self,
        portal_output: dict[str, Any],
        mega_output: dict[str, Any],
        portal_source_path: Path = DEFAULT_OUTPUT,
        mega_source_path: Path = DEFAULT_MERGED_INPUT,
    ) -> dict[str, Any]:
        portal_candidates = self._collect_candidates(portal_output)
        mega_candidates = self._collect_candidates(mega_output)
        merged_candidates = self._dedupe_candidates(portal_candidates + mega_candidates)
        merged_candidates = [self._normalize_candidate_for_db(candidate) for candidate in merged_candidates]
        merged_excluded = self._collect_excluded(portal_output) + self._collect_excluded(mega_output)
        merged_excluded = self._dedupe_excluded(merged_excluded)
        return {
            "generated_at": datetime.now().isoformat(),
            "source_files": {
                "portal_output": str(portal_source_path),
                "mega_final_export": str(mega_source_path),
            },
            "government_hackathons": merged_candidates,
            "excluded_opportunities": merged_excluded,
            "metadata": {
                "total_candidates": len(merged_candidates),
                "total_excluded": len(merged_excluded),
                "tags_injected": list(NORMALIZED_TAGS),
            },
        }

    def _prioritized_portals(self) -> list[PortalDefinition]:
        return sorted(MANDATORY_PORTALS, key=lambda portal: (portal.priority, portal.name))

    async def _scrape_portal(self, context: BrowserContext, portal: PortalDefinition) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        candidates: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []

        for path in self._expanded_paths(portal):
            page = await context.new_page()
            try:
                url = urljoin(portal.root_url, path)
                await self._load_page(page, url)
                await self._scroll_to_bottom(page)
                cards = await self._extract_candidate_cards(page, portal)
                for card in cards:
                    if self._is_valid_candidate(card):
                        candidates.append(card)
                    else:
                        excluded.append(card)
            finally:
                await page.close()

        return candidates, excluded

    def _expanded_paths(self, portal: PortalDefinition) -> list[str]:
        current_year = datetime.now().year
        expanded: list[str] = []
        for path in portal.discovery_paths:
            if "{currentyear}" in path:
                expanded.append(path.format(currentyear=current_year))
            else:
                expanded.append(path)
        return expanded or ["/"]

    async def _load_page(self, page: Page, url: str) -> None:
        await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        try:
            await page.wait_for_load_state("networkidle", timeout=self.timeout_ms)
        except Exception:
            pass

    async def _scroll_to_bottom(self, page: Page, steps: int = 8) -> None:
        for _ in range(steps):
            await page.mouse.wheel(0, 1800)
            await page.wait_for_timeout(400)

    async def _extract_candidate_cards(self, page: Page, portal: PortalDefinition) -> list[dict[str, Any]]:
        rows = await page.locator("a, article, section, li, div").evaluate_all(
            """
            (nodes) => nodes.map((node) => {
                const text = (node.innerText || node.textContent || '').trim();
                const links = Array.from(node.querySelectorAll('a')).map((a) => a.href).filter(Boolean);
                return {
                    text,
                    links,
                    href: node.href || '',
                    tag: node.tagName,
                };
            }).filter((row) => row.text && row.text.length > 20)
            """
        )

        results: list[dict[str, Any]] = []
        page_url = page.url
        for row in rows:
            text = normalize_space(row.get("text", ""))
            if not self._looks_like_technical_competition(text):
                continue
            extracted = self._build_candidate_from_text(text, page_url, portal, row.get("links", []))
            if extracted.get("source_url") and extracted["source_url"] in self.cache.rejected_urls:
                continue
            results.append(extracted)
            self.cache.remember_url(extracted.get("source_url", ""))
            self.cache.remember_title(extracted.get("hackathon_name", ""))
        return results

    def _build_candidate_from_text(self, text: str, source_url: str, portal: PortalDefinition, links: Iterable[str]) -> dict[str, Any]:
        title = self._derive_title(text)
        registration_url = self._pick_url(links, ("register", "apply", "signup", "submission"))
        detail_url = self._pick_url(links, ("challenge", "hackathon", "event", "initiative", "details")) or source_url
        status = self._derive_status(text)
        candidate = ChallengeCandidate(
            event_type=self._derive_event_type(text),
            hackathon_name=title,
            full_name=title,
            current_status=status,
            registration_url=registration_url,
            submission_url=registration_url,
            official_website=portal.root_url,
            official_event_page=detail_url,
            source_url=source_url,
            hosting_organization=portal.name,
            ministry=self._derive_ministry(portal, text),
            institution_type=self._derive_institution_type(portal),
            platform=self._derive_platform(portal.root_url),
            domain=urlparse(portal.root_url).netloc,
            theme=self._derive_theme(text),
            sdg_alignment=self._extract_sdg_alignment(text),
            focus_areas=self._extract_focus_areas(text),
            problem_statements=self._extract_problem_statements(text),
            deadline=self._extract_deadline(text),
            eligibility_criteria={"summary": self._extract_sentence(text, ("eligibility", "eligible"))},
            team_size={"minimum": None, "maximum": None},
            submission_fee={"amount": None, "currency": "INR", "display": ""},
            prizes={"summary": self._extract_sentence(text, ("prize", "reward", "cash"))},
            tags=self._derive_tags(text, portal),
            source_validation={
                "source_type": "playwright_dom",
                "official_confirmation_found": portal.is_aggregator or portal.root_url.startswith("https://"),
                "official_open_keywords_found": self._active_keywords(text),
                "deadline_verified": bool(self._extract_deadline(text)),
            },
        )
        return candidate.to_dict()

    def _is_valid_candidate(self, candidate: dict[str, Any]) -> bool:
        text_blob = " ".join(str(candidate.get(key, "")) for key in ("hackathon_name", "current_status", "theme", "full_name"))
        if not candidate.get("official_event_page") and not candidate.get("official_website"):
            return False
        if not self._looks_like_technical_competition(text_blob):
            return False
        if candidate.get("source_url") and candidate["source_url"] in self.cache.rejected_urls:
            return False
        if candidate.get("hackathon_name") and candidate["hackathon_name"].lower() in self.cache.seen_titles:
            return False
        return candidate.get("current_status", "").lower() not in {"closed", "archived"}

    def _dedupe_candidates(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[tuple[str, str]] = set()
        unique: list[dict[str, Any]] = []
        for candidate in candidates:
            key = (
                normalize_space(candidate.get("hackathon_name", "")).lower(),
                normalize_space(candidate.get("source_url", "")),
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(candidate)
        return unique

    def _dedupe_excluded(self, excluded: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[tuple[str, str]] = set()
        unique: list[dict[str, Any]] = []
        for item in excluded:
            key = (
                normalize_space(item.get("hackathon_name", "")).lower(),
                normalize_space(item.get("source_url", "")),
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        return unique

    def _collect_candidates(self, payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        candidates = payload.get("government_hackathons", [])
        return [item for item in candidates if isinstance(item, dict)]

    def _collect_excluded(self, payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        excluded = payload.get("excluded_opportunities", [])
        return [item for item in excluded if isinstance(item, dict)]

    def _normalize_candidate_for_db(self, candidate: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(candidate)
        tags = normalized.get("tags", [])
        if not isinstance(tags, list):
            tags = [str(tags)] if tags else []
        tags.extend(NORMALIZED_TAGS)
        normalized["tags"] = sorted({normalize_space(tag).lower() for tag in tags if normalize_space(tag)})
        normalized["hackathon_name"] = normalize_space(normalized.get("hackathon_name", ""))
        normalized["full_name"] = normalize_space(normalized.get("full_name", normalized.get("hackathon_name", "")))
        normalized["current_status"] = normalize_space(normalized.get("current_status", "")).lower() or "unknown"
        normalized["source_url"] = normalize_space(normalized.get("source_url", ""))
        normalized["official_event_page"] = normalize_space(normalized.get("official_event_page", ""))
        normalized["official_website"] = normalize_space(normalized.get("official_website", ""))
        normalized["registration_url"] = normalize_space(normalized.get("registration_url", ""))
        normalized["submission_url"] = normalize_space(normalized.get("submission_url", ""))
        normalized["domain"] = normalize_space(normalized.get("domain", "")).lower()
        sdg_alignment = normalized.get("sdg_alignment", [])
        if not isinstance(sdg_alignment, list):
            sdg_alignment = [str(sdg_alignment)] if sdg_alignment else []
        normalized["sdg_alignment"] = sorted({normalize_space(str(sdg)) for sdg in sdg_alignment if normalize_space(str(sdg))})
        return normalized

    def build_recursive_discovery_prompt(self, candidates: list[dict[str, Any]], excluded: list[dict[str, Any]]) -> str:
        discovered_urls = {normalize_space(item.get("source_url", "")) for item in candidates + excluded}
        discovered_names = {normalize_space(item.get("hackathon_name", "")) for item in candidates + excluded}
        return (
            "Find additional currently active Indian government technical competitions NOT already discovered. "
            f"Exclude URLs: {sorted(url for url in discovered_urls if url)}. "
            f"Exclude names: {sorted(name for name in discovered_names if name)}. "
            "Avoid rediscovery loops. Prioritize official ministry portals and central aggregators."
        )

    @staticmethod
    def _looks_like_technical_competition(text: str) -> bool:
        lowered = text.lower()
        return any(hint in lowered for hint in ACTIVE_HINTS)

    @staticmethod
    def _derive_title(text: str) -> str:
        for pattern in (
            r"(?:challenge|hackathon|competition|initiative)[:\-\s]+(.{4,120})",
            r"^(.{6,120}?)(?:\s+[-|:]\s+.*)?$",
        ):
            match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
            if match:
                return normalize_space(match.group(1))
        return normalize_space(text[:120])

    @staticmethod
    def _derive_status(text: str) -> str:
        lowered = text.lower()
        if any(hint in lowered for hint in CLOSED_HINTS):
            return "closed"
        if any(hint in lowered for hint in ACTIVE_HINTS):
            return "open"
        return "unknown"

    @staticmethod
    def _derive_event_type(text: str) -> str:
        lowered = text.lower()
        if "cyber" in lowered:
            return "cybersecurity competition"
        if "defence" in lowered or "defense" in lowered:
            return "defence innovation competition"
        if "ai" in lowered or "artificial intelligence" in lowered:
            return "ai challenge"
        if "hackathon" in lowered:
            return "hackathon"
        return "technical challenge"

    @staticmethod
    def _derive_ministry(portal: PortalDefinition, text: str) -> str:
        if portal.is_aggregator:
            return portal.name
        ministry_matches = re.findall(r"(?:ministry of|department of|moe|meity|mha|rbi|nic)\s+[A-Za-z&\-\s]+", text, flags=re.IGNORECASE)
        return normalize_space(ministry_matches[0]) if ministry_matches else portal.name

    @staticmethod
    def _derive_institution_type(portal: PortalDefinition) -> str:
        domain = urlparse(portal.root_url).netloc.lower()
        if domain.endswith(".gov.in"):
            return "government portal"
        if domain.endswith(".org"):
            return "government-backed aggregator"
        return "official portal"

    @staticmethod
    def _derive_platform(root_url: str) -> str:
        domain = urlparse(root_url).netloc.lower()
        if "mygov" in domain:
            return "MyGov"
        if "data.gov" in domain:
            return "OGD"
        if "sih" in domain:
            return "SIH"
        return "web"

    @staticmethod
    def _derive_theme(text: str) -> str:
        for keyword in ("ai", "cyber", "defence", "health", "agri", "smart city", "innovation", "engineering"):
            if keyword in text.lower():
                return keyword.title()
        return ""

    @staticmethod
    def _extract_focus_areas(text: str) -> list[str]:
        areas: list[str] = []
        for keyword in ("AI", "Cybersecurity", "Defence", "Data Science", "Smart Cities", "Agriculture", "Health", "Innovation"):
            if keyword.lower() in text.lower():
                areas.append(keyword)
        return areas

    @staticmethod
    def _extract_problem_statements(text: str) -> list[str]:
        sentences = re.split(r"(?<=[.!?])\s+", text)
        return [sentence.strip() for sentence in sentences if any(token in sentence.lower() for token in ("problem", "statement", "challenge"))][:5]

    @staticmethod
    def _extract_sdg_alignment(text: str) -> list[str]:
        lowered = text.lower()
        sdg_keywords = {
            "SDG 2: Zero Hunger": ("agri", "agriculture", "food security", "nutrition", "farmer"),
            "SDG 3: Good Health and Well-being": ("health", "medical", "hospital", "wellness", "telemedicine"),
            "SDG 4: Quality Education": ("education", "learning", "edtech", "school", "student"),
            "SDG 6: Clean Water and Sanitation": ("water", "sanitation", "wastewater", "jal"),
            "SDG 7: Affordable and Clean Energy": ("energy", "renewable", "solar", "wind", "battery"),
            "SDG 8: Decent Work and Economic Growth": ("employment", "jobs", "skill development", "msme"),
            "SDG 9: Industry, Innovation and Infrastructure": ("innovation", "manufacturing", "infrastructure", "industry"),
            "SDG 10: Reduced Inequalities": ("inclusion", "accessibility", "divyang", "social equity"),
            "SDG 11: Sustainable Cities and Communities": ("smart city", "urban", "mobility", "transport", "housing"),
            "SDG 12: Responsible Consumption and Production": ("recycling", "circular economy", "waste management"),
            "SDG 13: Climate Action": ("climate", "carbon", "emission", "sustainability", "green"),
            "SDG 14: Life Below Water": ("marine", "ocean", "coastal", "fisheries"),
            "SDG 15: Life on Land": ("biodiversity", "forest", "wildlife", "land restoration"),
            "SDG 16: Peace, Justice and Strong Institutions": ("cybersecurity", "justice", "governance", "digital public infrastructure"),
            "SDG 17: Partnerships for the Goals": ("partnership", "collaboration", "co-creation", "ecosystem"),
        }
        matched: list[str] = []
        for sdg, keywords in sdg_keywords.items():
            if any(keyword in lowered for keyword in keywords):
                matched.append(sdg)
        return sorted(set(matched))

    @staticmethod
    def _extract_deadline(text: str) -> str:
        patterns = (
            r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
            r"\b\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}\b",
            r"\b[A-Za-z]{3,9}\s+\d{1,2},\s+\d{4}\b",
        )
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(0)
        return ""

    @staticmethod
    def _extract_sentence(text: str, keywords: Iterable[str]) -> str:
        sentences = re.split(r"(?<=[.!?])\s+", text)
        keyword_set = tuple(keyword.lower() for keyword in keywords)
        for sentence in sentences:
            if any(keyword in sentence.lower() for keyword in keyword_set):
                return sentence.strip()
        return ""

    @staticmethod
    def _derive_tags(text: str, portal: PortalDefinition) -> list[str]:
        tags = [portal.name, "government"]
        for keyword in ("ai", "cyber", "hackathon", "challenge", "innovation", "defence", "data"):
            if keyword in text.lower():
                tags.append(keyword)
        return sorted(set(tags))

    @staticmethod
    def _active_keywords(text: str) -> list[str]:
        lowered = text.lower()
        return [hint for hint in ACTIVE_HINTS if hint in lowered]

    @staticmethod
    def _pick_url(links: Iterable[str], tokens: Iterable[str]) -> str:
        lowered_tokens = tuple(token.lower() for token in tokens)
        for link in links:
            normalized_link = link.lower()
            if any(token in normalized_link for token in lowered_tokens):
                return link
        return ""


def normalize_space(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if loaded is not None else default
    except Exception:
        return default


def ensure_archive_dir() -> None:
    OUTPUT_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)


def cleanup_intermediate_outputs(paths: Iterable[Path]) -> None:
    for path in paths:
        if path.exists():
            path.unlink()


def merge_and_write_ready4db(
    portal_output_path: Path = DEFAULT_OUTPUT,
    mega_output_path: Path = DEFAULT_MERGED_INPUT,
    ready4db_path: Path = DEFAULT_READY4DB,
) -> dict[str, Any]:
    layer = CentralizedGovernmentHackathonIntelligenceLayer()
    portal_output = load_json(portal_output_path, default={})
    mega_output = load_json(mega_output_path, default={})
    merged = layer.build_ready4db(
        portal_output,
        mega_output,
        portal_source_path=portal_output_path,
        mega_source_path=mega_output_path,
    )
    ensure_archive_dir()
    write_json(ready4db_path, merged)
    return merged


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Centralized Government Hackathon Portal Intelligence Layer")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Path to write canonical candidate output")
    parser.add_argument("--excluded-output", default=str(DEFAULT_EXCLUDED), help="Path to write excluded opportunities")
    parser.add_argument("--mega-input", default=str(DEFAULT_MERGED_INPUT), help="Path to the existing mega_final_export.json input")
    parser.add_argument("--ready4db-output", default=str(DEFAULT_READY4DB), help="Archive path for the final deduped hackathon_ready4db.json")
    parser.add_argument("--merge-only", action="store_true", help="Skip scraping and only merge existing JSON inputs")
    parser.add_argument("--cleanup", action=argparse.BooleanOptionalAction, default=True, help="Remove intermediate JSON artifacts after writing the archive output")
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True, help="Run Playwright headless")
    parser.add_argument("--concurrency", type=int, default=4, help="Reserved concurrency for future tab fan-out")
    parser.add_argument("--timeout-ms", type=int, default=15000, help="Per-page timeout in milliseconds")
    args = parser.parse_args(argv)

    layer = CentralizedGovernmentHackathonIntelligenceLayer(
        headless=args.headless,
        concurrency=args.concurrency,
        timeout_ms=args.timeout_ms,
    )
    portal_output_path = Path(args.output)
    mega_output_path = Path(args.mega_input)
    ready4db_path = Path(args.ready4db_output)

    if args.merge_only:
        portal_output = load_json(portal_output_path, default={})
        mega_output = load_json(mega_output_path, default={})
        ready4db = layer.build_ready4db(
            portal_output,
            mega_output,
            portal_source_path=portal_output_path,
            mega_source_path=mega_output_path,
        )
    else:
        result = asyncio.run(layer.run())
        write_json(portal_output_path, result)
        write_json(Path(args.excluded_output), result.get("excluded_opportunities", []))
        mega_output = load_json(mega_output_path, default={})
        ready4db = layer.build_ready4db(
            result,
            mega_output,
            portal_source_path=portal_output_path,
            mega_source_path=mega_output_path,
        )

    ensure_archive_dir()
    write_json(ready4db_path, ready4db)

    if args.cleanup:
        cleanup_intermediate_outputs((portal_output_path, Path(args.excluded_output)))

    print(json.dumps({
        "output": args.output,
        "excluded_output": args.excluded_output,
        "ready4db_output": args.ready4db_output,
        "count": len(ready4db.get("government_hackathons", [])),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
