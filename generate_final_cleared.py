#!/usr/bin/env python3
"""Create hackathons_final_cleared.json from generated scraper output.

This is a second-pass validation layer. It removes proposal/R&D intake,
non-hackathon calls, duplicate records, and other false positives from the
already generated JSON export.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


DEFAULT_INPUTS = [
    Path("hackathons_results.json"),
    Path("output.json"),
    Path("defence_space_ai_opportunities.json"),
]
DEFAULT_OUTPUT = Path("hackathons_final_cleared.json")

HACKATHON_LIKE_KEYWORDS = (
    "hackathon",
    "challenge",
    "competition",
    "capture the flag",
    "ctf",
    "open challenge",
    "coding challenge",
    "datathon",
    "ai challenge",
    "cyber challenge",
    "technical challenge",
)

PROPOSAL_OR_R_AND_D_KEYWORDS = (
    "call for proposal",
    "call for proposals",
    "proposal",
    "r&d",
    "research and development",
    "research proposal",
    "joint innovation call",
    "request for proposal",
    "request for proposals",
    "cfp",
    "grant",
    "funding",
    "seed funding",
    "pre-seed",
    "research competition",
    "accelerator",
    "incubation",
    "incubator",
    "cohort",
    "fellowship",
    "scholarship",
    "procurement",
    "tender",
    "expression of interest",
    "eoi",
)

STARTUP_KEYWORDS = (
    "startup india",
    "startup india ams",
    "bharat startup",
    "startup challenge",
    "startup grand challenge",
    "startup competition",
    "tech startup challenge",
    "startup",
    "venture",
)

ACTIVE_STATUS_KEYWORDS = (
    "open",
    "active",
    "ongoing",
    "submission_open",
    "registration_open",
    "application_open",
)


@dataclass(frozen=True)
class CleanerStats:
    input_records: int = 0
    kept_records: int = 0
    removed_proposal_rnd: int = 0
    removed_non_hackathon: int = 0
    removed_duplicate: int = 0
    removed_inactive: int = 0
    removed_startup: int = 0


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).lower()
    text = text.replace("&", " and ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_title(value: Any) -> str:
    text = normalize_text(value)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, payload: Any) -> None:
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    temp_path.replace(path)


def iter_records(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item
        return

    if not isinstance(payload, dict):
        return

    for key in (
        "candidates",
        "government_hackathons",
        "borderline_opportunities",
        "final_cleared_hackathons",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield item


def record_text(record: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "hackathon_name",
        "full_name",
        "current_status",
        "event_type",
        "domain",
        "theme",
        "hosting_organization",
        "ministry",
        "platform",
        "source_url",
        "official_event_page",
        "official_website",
    ):
        value = record.get(key)
        if value:
            parts.append(str(value))

    eligibility = record.get("eligibility_criteria")
    if isinstance(eligibility, dict):
        summary = eligibility.get("summary")
        if summary:
            parts.append(str(summary))

    prizes = record.get("prizes")
    if isinstance(prizes, dict):
        summary = prizes.get("summary")
        if summary:
            parts.append(str(summary))

    source_validation = record.get("source_validation")
    if isinstance(source_validation, dict):
        for key in ("official_open_keywords_found", "official_confirmation_found"):
            value = source_validation.get(key)
            if value:
                parts.append(str(value))

    return normalize_text(" ".join(parts))


def record_status(record: dict[str, Any]) -> str:
    return normalize_text(record.get("current_status") or record.get("status") or "")


def is_hackathon_like(record: dict[str, Any]) -> bool:
    text = record_text(record)
    return any(keyword in text for keyword in HACKATHON_LIKE_KEYWORDS)


def is_proposal_or_rnd(record: dict[str, Any]) -> bool:
    text = record_text(record)
    status = record_status(record)
    explicit_proposal = any(keyword in text or keyword in status for keyword in PROPOSAL_OR_R_AND_D_KEYWORDS)
    return bool(explicit_proposal or "proposal" in status)


def is_startup_listing(record: dict[str, Any]) -> bool:
    text = record_text(record)
    if not any(keyword in text for keyword in STARTUP_KEYWORDS):
        return False

    startup_signals = (
        "startup india",
        "startup india ams",
        "bharat startup",
        "startup challenge",
        "startup grand challenge",
        "tech startup challenge",
    )
    return any(signal in text for signal in startup_signals)


def is_active(record: dict[str, Any]) -> bool:
    status = record_status(record)
    if not status:
        return True
    return any(keyword in status for keyword in ACTIVE_STATUS_KEYWORDS) or "open" in status


def dedupe_key(record: dict[str, Any]) -> str:
    canonical_url = normalize_text(
        record.get("source_url") or record.get("official_event_page") or record.get("registration_url") or ""
    )
    if canonical_url:
        return f"url:{canonical_url}"

    title = normalize_title(record.get("hackathon_name") or record.get("full_name") or "")
    org = normalize_title(record.get("hosting_organization") or record.get("ministry") or "")
    deadline = normalize_text(
        record.get("deadline")
        or record.get("submission_close_date")
        or record.get("application_deadline")
        or record.get("proposal_deadline")
        or ""
    )
    if title and org and deadline:
        return f"title_org_deadline:{title}:{org}:{deadline}"
    if title and org:
        return f"title_org:{title}:{org}"
    if title:
        return f"title:{title}"
    return f"raw:{normalize_text(json.dumps(record, sort_keys=True, ensure_ascii=False))}"


def clean_records(records: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], CleanerStats]:
    stats = CleanerStats()
    seen: set[str] = set()
    kept: list[dict[str, Any]] = []

    for record in records:
        stats = CleanerStats(
            input_records=stats.input_records + 1,
            kept_records=stats.kept_records,
            removed_proposal_rnd=stats.removed_proposal_rnd,
            removed_non_hackathon=stats.removed_non_hackathon,
            removed_duplicate=stats.removed_duplicate,
            removed_inactive=stats.removed_inactive,
            removed_startup=stats.removed_startup,
        )

        if record.get("classification_tier") in {"rejected", "archived"}:
            stats = CleanerStats(
                input_records=stats.input_records,
                kept_records=stats.kept_records,
                removed_proposal_rnd=stats.removed_proposal_rnd,
                removed_non_hackathon=stats.removed_non_hackathon,
                removed_duplicate=stats.removed_duplicate,
                removed_inactive=stats.removed_inactive + 1,
                removed_startup=stats.removed_startup,
            )
            continue

        if is_startup_listing(record):
            stats = CleanerStats(
                input_records=stats.input_records,
                kept_records=stats.kept_records,
                removed_proposal_rnd=stats.removed_proposal_rnd,
                removed_non_hackathon=stats.removed_non_hackathon,
                removed_duplicate=stats.removed_duplicate,
                removed_inactive=stats.removed_inactive,
                removed_startup=stats.removed_startup + 1,
            )
            continue

        if not is_hackathon_like(record):
            stats = CleanerStats(
                input_records=stats.input_records,
                kept_records=stats.kept_records,
                removed_proposal_rnd=stats.removed_proposal_rnd,
                removed_non_hackathon=stats.removed_non_hackathon + 1,
                removed_duplicate=stats.removed_duplicate,
                removed_inactive=stats.removed_inactive,
                removed_startup=stats.removed_startup,
            )
            continue

        if is_proposal_or_rnd(record):
            stats = CleanerStats(
                input_records=stats.input_records,
                kept_records=stats.kept_records,
                removed_proposal_rnd=stats.removed_proposal_rnd + 1,
                removed_non_hackathon=stats.removed_non_hackathon,
                removed_duplicate=stats.removed_duplicate,
                removed_inactive=stats.removed_inactive,
                removed_startup=stats.removed_startup,
            )
            continue

        if not is_active(record):
            stats = CleanerStats(
                input_records=stats.input_records,
                kept_records=stats.kept_records,
                removed_proposal_rnd=stats.removed_proposal_rnd,
                removed_non_hackathon=stats.removed_non_hackathon,
                removed_duplicate=stats.removed_duplicate,
                removed_inactive=stats.removed_inactive + 1,
                removed_startup=stats.removed_startup,
            )
            continue

        key = dedupe_key(record)
        if key in seen:
            stats = CleanerStats(
                input_records=stats.input_records,
                kept_records=stats.kept_records,
                removed_proposal_rnd=stats.removed_proposal_rnd,
                removed_non_hackathon=stats.removed_non_hackathon,
                removed_duplicate=stats.removed_duplicate + 1,
                removed_inactive=stats.removed_inactive,
                removed_startup=stats.removed_startup,
            )
            continue

        seen.add(key)
        kept.append(record)
        stats = CleanerStats(
            input_records=stats.input_records,
            kept_records=stats.kept_records + 1,
            removed_proposal_rnd=stats.removed_proposal_rnd,
            removed_non_hackathon=stats.removed_non_hackathon,
            removed_duplicate=stats.removed_duplicate,
            removed_inactive=stats.removed_inactive,
            removed_startup=stats.removed_startup,
        )

    return kept, stats


def build_output(source_payload: Any, records: list[dict[str, Any]], stats: CleanerStats, source_path: Path) -> dict[str, Any]:
    metadata = {
        "source_file": source_path.name,
        "total_input_records": stats.input_records,
        "total_cleared_records": stats.kept_records,
        "removed_proposal_rnd": stats.removed_proposal_rnd,
        "removed_non_hackathon": stats.removed_non_hackathon,
        "removed_duplicate": stats.removed_duplicate,
        "removed_inactive": stats.removed_inactive,
        "removed_startup": stats.removed_startup,
    }

    if isinstance(source_payload, dict) and isinstance(source_payload.get("metadata"), dict):
        metadata["source_metadata"] = source_payload["metadata"]

    return {
        "government_hackathons": records,
        "excluded_opportunities": [],
        "metadata": metadata,
    }


def resolve_input_path(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.exists():
            raise FileNotFoundError(f"Input file not found: {path}")
        return path

    for candidate in DEFAULT_INPUTS:
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        "No generated JSON found. Tried: " + ", ".join(str(candidate) for candidate in DEFAULT_INPUTS)
    )


def build_final_cleared_file(input_path: Path, output_path: Path) -> dict[str, Any]:
    source_payload = load_json(input_path)
    records = list(iter_records(source_payload))
    cleaned_records, stats = clean_records(records)
    output_payload = build_output(source_payload, cleaned_records, stats, input_path)
    save_json(output_path, output_payload)
    return {
        "output_path": output_path,
        "source_path": input_path,
        "stats": stats,
        "payload": output_payload,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate hackathons_final_cleared.json from generated JSON output.")
    parser.add_argument("--input", help="Input JSON file to clean. Defaults to hackathons_results.json, then output.json.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output JSON file path.")
    args = parser.parse_args()

    input_path = resolve_input_path(args.input)
    output_path = Path(args.output)

    result = build_final_cleared_file(input_path, output_path)
    stats: CleanerStats = result["stats"]

    print(f"Wrote {output_path}")
    print(f"Input records: {stats.input_records}")
    print(f"Kept records: {stats.kept_records}")
    print(f"Removed proposal/R&D: {stats.removed_proposal_rnd}")
    print(f"Removed startup listings: {stats.removed_startup}")
    print(f"Removed non-hackathon: {stats.removed_non_hackathon}")
    print(f"Removed duplicates: {stats.removed_duplicate}")
    print(f"Removed inactive: {stats.removed_inactive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
