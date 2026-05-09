#!/usr/bin/env python3
"""
Analytical coverage estimation for the opportunity intelligence pipeline.

This module does not discover opportunities. It audits the evidence already
collected by the runner and estimates how likely the run was to miss major
active opportunities.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


HIGH_SIGNAL_PORTALS = [
    "mygov.in",
    "startupindia.gov.in",
    "idex.gov.in",
    "indiaai.gov.in",
    "aikosh.indiaai.gov.in",
    "drdo.gov.in",
    "isro.gov.in",
    "dst.gov.in",
    "dbtindia.gov.in",
    "birac.nic.in",
    "meity.gov.in",
    "sih.gov.in",
]


@dataclass
class PortalCoverageRecord:
    domain: str
    category: str
    visited: bool = False
    accessible: bool = False
    timed_out: bool = False
    events_found: int = 0
    active_events_found: int = 0
    search_queries_used: list[str] = field(default_factory=list)
    models_used: list[str] = field(default_factory=list)
    validation_failures: list[str] = field(default_factory=list)
    last_scan_timestamp: str | None = None
    coverage_signal: str = "not_attempted"


@dataclass
class SearchObservation:
    query: str
    new_events_discovered: int = 0
    duplicate_events_discovered: int = 0
    rejected_events_discovered: int = 0


@dataclass
class SearchSaturationTracker:
    queries_executed: list[str] = field(default_factory=list)
    new_events_discovered: int = 0
    duplicate_events_discovered: int = 0
    rejected_events_discovered: int = 0
    observations: list[SearchObservation] = field(default_factory=list)

    def record_query_batch(
        self,
        queries: list[str],
        new_events_discovered: int,
        duplicate_events_discovered: int,
        rejected_events_discovered: int,
    ) -> None:
        clean_queries = [str(query) for query in queries if str(query).strip()]
        if not clean_queries:
            clean_queries = ["unknown_query_batch"]
        self.queries_executed.extend(clean_queries)
        self.new_events_discovered += max(0, new_events_discovered)
        self.duplicate_events_discovered += max(0, duplicate_events_discovered)
        self.rejected_events_discovered += max(0, rejected_events_discovered)
        new_parts = self._spread_count(new_events_discovered, len(clean_queries))
        duplicate_parts = self._spread_count(duplicate_events_discovered, len(clean_queries))
        rejected_parts = self._spread_count(rejected_events_discovered, len(clean_queries))
        for index, query in enumerate(clean_queries):
            self.observations.append(
                SearchObservation(
                    query=query,
                    new_events_discovered=new_parts[index],
                    duplicate_events_discovered=duplicate_parts[index],
                    rejected_events_discovered=rejected_parts[index],
                )
            )

    def _spread_count(self, total: int, buckets: int) -> list[int]:
        buckets = max(1, buckets)
        total = max(0, total)
        base, remainder = divmod(total, buckets)
        return [base + (1 if index < remainder else 0) for index in range(buckets)]

    @property
    def discovery_velocity(self) -> float:
        if not self.observations:
            return 0.0
        window = self.observations[-25:]
        return round(sum(item.new_events_discovered for item in window) / len(window), 4)

    @property
    def duplicate_discovery_ratio(self) -> float:
        total = self.new_events_discovered + self.duplicate_events_discovered + self.rejected_events_discovered
        if total <= 0:
            return 0.0
        return round((self.duplicate_events_discovered + self.rejected_events_discovered) / total, 4)

    @property
    def search_saturation_reached(self) -> bool:
        if len(self.observations) < 25:
            return False
        window = self.observations[-25:]
        new_total = sum(item.new_events_discovered for item in window)
        duplicate_or_rejected = sum(
            item.duplicate_events_discovered + item.rejected_events_discovered for item in window
        )
        total = new_total + duplicate_or_rejected
        if len(window) >= 25 and new_total == 0:
            return True
        return total > 0 and duplicate_or_rejected / total > 0.80

    def snapshot(self) -> dict[str, Any]:
        return {
            "queries_executed": len(self.queries_executed),
            "new_events_discovered": self.new_events_discovered,
            "duplicate_events_discovered": self.duplicate_events_discovered,
            "rejected_events_discovered": self.rejected_events_discovered,
            "discovery_velocity": self.discovery_velocity,
            "duplicate_discovery_ratio": self.duplicate_discovery_ratio,
            "search_saturation_reached": self.search_saturation_reached,
        }


class CoverageIntelligenceEngine:
    def __init__(
        self,
        history_path: Path,
        high_signal_portals: list[str] | None = None,
    ) -> None:
        self.history_path = history_path
        self.high_signal_portals = high_signal_portals or HIGH_SIGNAL_PORTALS

    def analyze(
        self,
        final_payload: dict[str, Any],
        discovery_tasks: list[Any],
        payloads: list[dict[str, Any]],
        artifacts: list[Any],
        saturation_tracker: SearchSaturationTracker,
        models_attempted: list[str],
    ) -> dict[str, Any]:
        records = self._portal_records(final_payload, discovery_tasks, payloads, artifacts)
        history = self._load_history()
        historical_baseline_delta = self._historical_baseline_delta(final_payload, history)
        possible_missed_domains = self._possible_missed_domains(records, history)
        coverage_warnings = self._coverage_warnings(
            records=records,
            history=history,
            saturation_tracker=saturation_tracker,
            models_attempted=models_attempted,
            possible_missed_domains=possible_missed_domains,
        )
        multi_model_overlap = self._multi_model_overlap(final_payload)
        high_signal_scanned = sum(1 for portal in self.high_signal_portals if records[portal].visited)
        failed_portals = [
            record
            for record in records.values()
            if record.visited and (record.timed_out or not record.accessible)
        ]
        timeout_count = sum(1 for record in records.values() if record.visited and record.timed_out)
        high_signal_empty_count = sum(
            1
            for domain in self.high_signal_portals
            if records[domain].visited and records[domain].accessible and records[domain].active_events_found == 0
        )
        high_signal_coverage = high_signal_scanned / max(1, len(self.high_signal_portals))
        coverage_confidence = estimate_coverage_confidence(
            portals_scanned=sum(1 for record in records.values() if record.visited),
            failed_portals=len(failed_portals),
            saturation_state=saturation_tracker.search_saturation_reached,
            multi_model_overlap=multi_model_overlap,
            discovery_velocity=saturation_tracker.discovery_velocity,
            high_signal_coverage=high_signal_coverage,
            historical_baseline_delta=historical_baseline_delta,
            timeout_count=timeout_count,
            high_signal_empty_count=high_signal_empty_count,
        )
        coverage_status = self._coverage_status(coverage_confidence)
        portal_dump = {
            domain: asdict(records[domain])
            for domain in sorted(records)
            if records[domain].visited or domain in self.high_signal_portals
        }
        return {
            "coverage_confidence": coverage_confidence,
            "coverage_status": coverage_status,
            "search_saturation_reached": saturation_tracker.search_saturation_reached,
            "high_signal_portals_scanned": high_signal_scanned,
            "failed_portals": len(failed_portals),
            "portal_timeout_count": timeout_count,
            "high_signal_empty_count": high_signal_empty_count,
            "duplicate_discovery_ratio": saturation_tracker.duplicate_discovery_ratio,
            "multi_model_consensus_enabled": len([model for model in models_attempted if model]) > 1,
            "possible_missed_domains": possible_missed_domains,
            "coverage_warnings": coverage_warnings,
            "historical_baseline_delta": historical_baseline_delta,
            "discovery_velocity": saturation_tracker.discovery_velocity,
            "multi_model_overlap": multi_model_overlap,
            "portal_coverage": portal_dump,
            "saturation_metrics": saturation_tracker.snapshot(),
        }

    def persist_history(self, final_payload: dict[str, Any]) -> None:
        metadata = final_payload.get("metadata", {}) if isinstance(final_payload, dict) else {}
        analysis = metadata.get("coverage_analysis", {}) if isinstance(metadata, dict) else {}
        portal_coverage = analysis.get("portal_coverage", {}) if isinstance(analysis, dict) else {}
        snapshot = {
            "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
            "search_date": metadata.get("search_date") or metadata.get("current_date_used_for_validation"),
            "total_active_events": metadata.get("total_active_hackathons", 0),
            "portal_coverage": {
                domain: {
                    "events_found": record.get("events_found", 0),
                    "active_events_found": record.get("active_events_found", 0),
                    "visited": record.get("visited", False),
                    "accessible": record.get("accessible", False),
                    "timed_out": record.get("timed_out", False),
                }
                for domain, record in portal_coverage.items()
                if isinstance(record, dict)
            },
            "model_overlap": analysis.get("multi_model_overlap", {}),
            "failed_domains": [
                item.get("domain")
                for item in analysis.get("possible_missed_domains", [])
                if isinstance(item, dict) and item.get("domain")
            ],
            "discovery_counts": {
                "active": metadata.get("total_active_hackathons", 0),
                "borderline": metadata.get("total_borderline_opportunities", 0),
                "excluded": metadata.get("total_excluded", 0),
            },
        }
        history = self._load_history()
        runs = [item for item in history.get("runs", []) if isinstance(item, dict)]
        runs.append(snapshot)
        history = {"runs": runs[-20:]}
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(history)

    def _portal_records(
        self,
        final_payload: dict[str, Any],
        discovery_tasks: list[Any],
        payloads: list[dict[str, Any]],
        artifacts: list[Any],
    ) -> dict[str, PortalCoverageRecord]:
        metadata = final_payload.get("metadata", {}) if isinstance(final_payload, dict) else {}
        timestamp = (
            metadata.get("search_date")
            or metadata.get("current_date_used_for_validation")
            or datetime.now(UTC).date().isoformat()
        )
        records: dict[str, PortalCoverageRecord] = {
            domain: PortalCoverageRecord(
                domain=domain,
                category="high_signal",
                last_scan_timestamp=timestamp,
            )
            for domain in self.high_signal_portals
        }
        task_status = self._task_status(artifacts)
        for task in discovery_tasks:
            category = str(getattr(task, "category", "unknown") or "unknown")
            task_name = str(getattr(task, "name", "") or "")
            timed_out = bool(task_status.get(task_name, {}).get("timed_out"))
            accessible = bool(task_status.get(task_name, {}).get("success"))
            models = task_status.get(task_name, {}).get("models", [])
            queries = [str(query) for query in getattr(task, "queries", [])]
            domains = set()
            for url in getattr(task, "seed_urls", []):
                domain = _hostname(url)
                if domain:
                    domains.add(domain)
            for query in queries:
                domains.update(_domains_in_text(query))
            for domain in self._expand_high_signal_domains(domains):
                record = records.setdefault(
                    domain,
                    PortalCoverageRecord(domain=domain, category=category, last_scan_timestamp=timestamp),
                )
                record.visited = True
                record.accessible = record.accessible or accessible
                record.timed_out = record.timed_out or timed_out
                record.search_queries_used = sorted(set(record.search_queries_used + queries))
                record.models_used = sorted(set(record.models_used + models))
                record.coverage_signal = "task_completed" if accessible else "task_failed_or_timed_out"

        sources_scanned = set()
        for url in _ensure_list(metadata.get("sources_scanned")):
            sources_scanned.add(str(url))
        for payload in payloads:
            for url in _ensure_list(payload.get("sources_scanned")):
                sources_scanned.add(str(url))
            payload_metadata = payload.get("metadata", {}) if isinstance(payload.get("metadata"), dict) else {}
            for url in _ensure_list(payload_metadata.get("sources_scanned")):
                sources_scanned.add(str(url))

        for url in sources_scanned:
            domain = _hostname(url)
            if not domain:
                continue
            for coverage_domain in self._expand_high_signal_domains({domain}):
                record = records.setdefault(
                    coverage_domain,
                    PortalCoverageRecord(
                        domain=coverage_domain,
                        category="observed_source",
                        last_scan_timestamp=timestamp,
                    ),
                )
                record.visited = True
                record.accessible = True
                record.coverage_signal = "source_scanned"

        self._count_events(records, final_payload, "government_hackathons", active=True)
        self._count_events(records, final_payload, "borderline_opportunities", active=False)
        self._count_events(records, final_payload, "ecosystem_opportunities", active=False)
        self._count_events(records, final_payload, "archived_opportunities", active=False)
        self._finalize_coverage_signals(records)
        return records

    def _count_events(
        self,
        records: dict[str, PortalCoverageRecord],
        final_payload: dict[str, Any],
        key: str,
        active: bool,
    ) -> None:
        for event in _ensure_list(final_payload.get(key)):
            if not isinstance(event, dict):
                continue
            domains = {
                _hostname(event.get("source_url")),
                _hostname(event.get("official_event_page")),
                _hostname(event.get("official_website")),
                _hostname(event.get("registration_url")),
            }
            domains.discard("")
            domains = self._expand_high_signal_domains(domains)
            failures = _ensure_list(
                (event.get("source_validation") or {}).get("validation_errors")
                if isinstance(event.get("source_validation"), dict)
                else []
            )
            provenance = event.get("discovery_provenance") if isinstance(event.get("discovery_provenance"), dict) else {}
            event_models = [
                _compact_model_name(model)
                for model in _ensure_list(provenance.get("discovered_by_models"))
                if model
            ]
            query_count = int(provenance.get("discovery_query_count", 0) or 0)
            for domain in domains:
                record = records.setdefault(
                    domain,
                    PortalCoverageRecord(domain=domain, category="event_source"),
                )
                record.visited = True
                record.accessible = True
                record.events_found += 1
                if active:
                    record.active_events_found += 1
                record.validation_failures = sorted(set(record.validation_failures + [str(item) for item in failures]))
                record.models_used = sorted(set(record.models_used + event_models))
                if query_count:
                    record.search_queries_used = sorted(
                        set(record.search_queries_used + [f"{query_count}_event_discovery_queries"])
                    )
                record.coverage_signal = "active_event_found" if active else "borderline_event_found"

    def _finalize_coverage_signals(self, records: dict[str, PortalCoverageRecord]) -> None:
        for record in records.values():
            if not record.visited:
                record.coverage_signal = "not_attempted"
            elif record.timed_out:
                record.coverage_signal = "timed_out"
            elif not record.accessible:
                record.coverage_signal = "unreachable_or_failed"
            elif record.events_found == 0:
                record.coverage_signal = "zero_events_detected"
            elif record.active_events_found == 0:
                record.coverage_signal = "zero_active_events_detected"

    def _expand_high_signal_domains(self, domains: set[str]) -> set[str]:
        expanded = {domain for domain in domains if domain}
        for domain in list(expanded):
            for portal in self.high_signal_portals:
                if domain == portal or domain.endswith("." + portal):
                    expanded.add(portal)
        return expanded

    def _task_status(self, artifacts: list[Any]) -> dict[str, dict[str, Any]]:
        status: dict[str, dict[str, Any]] = {}
        for artifact in artifacts:
            task_name = str(getattr(artifact, "task_name", "") or "")
            if not task_name:
                continue
            item = status.setdefault(task_name, {"success": False, "timed_out": False, "models": []})
            item["success"] = item["success"] or bool(getattr(artifact, "success", False))
            item["timed_out"] = item["timed_out"] or bool(getattr(artifact, "timed_out", False))
            model = _compact_model_name(getattr(artifact, "model", ""))
            if model:
                item["models"] = sorted(set(item["models"] + [model]))
        return status

    def _possible_missed_domains(
        self,
        records: dict[str, PortalCoverageRecord],
        history: dict[str, Any],
    ) -> list[dict[str, str]]:
        missed: list[dict[str, str]] = []
        for domain in self.high_signal_portals:
            record = records[domain]
            reason = ""
            if not record.visited:
                reason = "not_scanned"
            elif record.timed_out:
                reason = "timeout"
            elif not record.accessible:
                reason = "unreachable_or_no_successful_task"
            elif record.active_events_found == 0:
                reason = "no_active_results_detected"
            if reason:
                missed.append({"domain": domain, "reason": reason})

        historical_active = self._historically_active_domains(history)
        for domain in sorted(historical_active):
            record = records.get(domain)
            if not record or not record.visited:
                item = {"domain": domain, "reason": "historical_activity_not_scanned"}
                if item not in missed:
                    missed.append(item)
            elif record.active_events_found == 0:
                item = {"domain": domain, "reason": "historical_activity_now_zero"}
                if item not in missed:
                    missed.append(item)
        return missed

    def _coverage_warnings(
        self,
        records: dict[str, PortalCoverageRecord],
        history: dict[str, Any],
        saturation_tracker: SearchSaturationTracker,
        models_attempted: list[str],
        possible_missed_domains: list[dict[str, str]],
    ) -> list[str]:
        warnings: list[str] = []
        timed_out_high_signal = [
            item["domain"]
            for item in possible_missed_domains
            if item.get("reason") == "timeout" and item.get("domain") in self.high_signal_portals
        ]
        historical_timeouts = self._historical_timeout_counts(history)
        if len(timed_out_high_signal) >= 2:
            warnings.append("multiple high-signal portals timed out")
        for item in possible_missed_domains:
            domain = item["domain"]
            reason = item["reason"]
            if reason == "historical_activity_now_zero":
                warnings.append(f"{domain} returned zero active events despite historical activity")
            elif reason == "historical_activity_not_scanned":
                warnings.append(f"{domain} was not scanned despite historical activity")
            elif reason == "no_active_results_detected" and domain in self.high_signal_portals:
                warnings.append(f"{domain} returned zero active events in current coverage audit")
            elif reason == "timeout":
                warnings.append(f"{domain} timed out during current coverage audit")
                if historical_timeouts.get(domain, 0) > 0:
                    warnings.append(f"{domain} repeatedly timed out across coverage runs")
            elif reason == "not_scanned":
                warnings.append(f"{domain} was not scanned during current coverage audit")
        if not saturation_tracker.search_saturation_reached:
            warnings.append("search saturation not reached before termination")
        overlap = self._multi_model_overlap_from_records(records)
        if len(models_attempted) > 1 and overlap.get("weak", 0) > max(1, overlap.get("strong", 0) + overlap.get("medium", 0)):
            warnings.append("single-model discoveries dominate current run")
        historical_active = self._historically_active_domains(history)
        for domain in sorted(historical_active):
            record = records.get(domain)
            if not record or not record.visited:
                warning = f"{domain} was not scanned despite historical activity"
                if warning not in warnings:
                    warnings.append(warning)
            elif record.active_events_found == 0:
                warning = f"{domain} returned zero active events despite historical activity"
                if warning not in warnings:
                    warnings.append(warning)
        return sorted(dict.fromkeys(warnings))

    def _multi_model_overlap(self, final_payload: dict[str, Any]) -> dict[str, Any]:
        counts = {"strong": 0, "medium": 0, "weak": 0, "none": 0}
        model_event_counts: dict[str, int] = {}
        for key in (
            "government_hackathons",
            "borderline_opportunities",
            "ecosystem_opportunities",
            "archived_opportunities",
        ):
            for event in _ensure_list(final_payload.get(key)):
                if not isinstance(event, dict):
                    continue
                provenance = event.get("discovery_provenance") if isinstance(event.get("discovery_provenance"), dict) else {}
                models = sorted({_compact_model_name(model) for model in _ensure_list(provenance.get("discovered_by_models")) if model})
                strength = consensus_strength(models)
                counts[strength] += 1
                for model in models:
                    model_event_counts[model] = model_event_counts.get(model, 0) + 1
        total = sum(counts.values())
        return {
            "total_events_with_provenance": total,
            "strong": counts["strong"],
            "medium": counts["medium"],
            "weak": counts["weak"],
            "none": counts["none"],
            "model_event_counts": dict(sorted(model_event_counts.items())),
        }

    def _multi_model_overlap_from_records(self, records: dict[str, PortalCoverageRecord]) -> dict[str, int]:
        counts = {"strong": 0, "medium": 0, "weak": 0, "none": 0}
        for record in records.values():
            if record.events_found <= 0:
                continue
            counts[consensus_strength(record.models_used)] += record.events_found
        return counts

    def _historical_baseline_delta(self, final_payload: dict[str, Any], history: dict[str, Any]) -> float:
        runs = [item for item in history.get("runs", []) if isinstance(item, dict)]
        if not runs:
            return 0.0
        recent = runs[-5:]
        baseline_values = [int(item.get("total_active_events", 0) or 0) for item in recent]
        baseline = sum(baseline_values) / max(1, len(baseline_values))
        current = int((final_payload.get("metadata") or {}).get("total_active_hackathons", 0) or 0)
        if baseline <= 0:
            return 0.0
        return round((current - baseline) / baseline, 4)

    def _historically_active_domains(self, history: dict[str, Any]) -> set[str]:
        domains: set[str] = set()
        for run in history.get("runs", [])[-10:]:
            if not isinstance(run, dict):
                continue
            coverage = run.get("portal_coverage", {})
            if not isinstance(coverage, dict):
                continue
            for domain, record in coverage.items():
                if isinstance(record, dict) and int(record.get("active_events_found", 0) or 0) > 0:
                    domains.add(str(domain))
        return domains

    def _historical_timeout_counts(self, history: dict[str, Any]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for run in history.get("runs", [])[-10:]:
            if not isinstance(run, dict):
                continue
            coverage = run.get("portal_coverage", {})
            if not isinstance(coverage, dict):
                continue
            for domain, record in coverage.items():
                if isinstance(record, dict) and record.get("timed_out"):
                    key = str(domain)
                    counts[key] = counts.get(key, 0) + 1
        return counts

    def _load_history(self) -> dict[str, Any]:
        try:
            with self.history_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {"runs": []}
        if not isinstance(data, dict):
            return {"runs": []}
        data.setdefault("runs", [])
        return data

    def _atomic_write(self, payload: dict[str, Any]) -> None:
        temp_path = self.history_path.with_suffix(self.history_path.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        temp_path.replace(self.history_path)

    def _coverage_status(self, coverage_confidence: float) -> str:
        if coverage_confidence < 0.50:
            return "low_confidence"
        if coverage_confidence <= 0.75:
            return "moderate_confidence"
        return "high_confidence"


def estimate_coverage_confidence(
    portals_scanned: int,
    failed_portals: int,
    saturation_state: bool,
    multi_model_overlap: dict[str, Any],
    discovery_velocity: float,
    high_signal_coverage: float,
    historical_baseline_delta: float,
    timeout_count: int = 0,
    high_signal_empty_count: int = 0,
) -> float:
    confidence = 0.42
    confidence += min(0.24, max(0.0, high_signal_coverage) * 0.24)
    confidence += min(0.10, portals_scanned / 100)
    confidence += 0.12 if saturation_state else -0.08
    confidence += min(0.08, max(0.0, 1.0 - discovery_velocity) * 0.08)

    total_overlap = int(multi_model_overlap.get("total_events_with_provenance", 0) or 0)
    strong = int(multi_model_overlap.get("strong", 0) or 0)
    medium = int(multi_model_overlap.get("medium", 0) or 0)
    weak = int(multi_model_overlap.get("weak", 0) or 0)
    if total_overlap:
        confidence += ((strong * 1.0 + medium * 0.65 + weak * 0.25) / total_overlap) * 0.12
    else:
        confidence -= 0.04

    confidence -= min(0.18, failed_portals * 0.025)
    confidence -= min(0.12, timeout_count * 0.035)
    confidence -= min(0.16, high_signal_empty_count * 0.018)
    if historical_baseline_delta < -0.20:
        confidence -= min(0.12, abs(historical_baseline_delta) * 0.18)
    elif historical_baseline_delta >= -0.10:
        confidence += 0.03
    return round(max(0.0, min(1.0, confidence)), 4)


def consensus_strength(models: list[str]) -> str:
    count = len({model for model in models if model})
    if count >= 3:
        return "strong"
    if count == 2:
        return "medium"
    if count == 1:
        return "weak"
    return "none"


def attach_consensus_to_event(event: dict[str, Any]) -> dict[str, Any]:
    provenance = event.get("discovery_provenance")
    if not isinstance(provenance, dict):
        provenance = {}
    models = sorted({_compact_model_name(model) for model in _ensure_list(provenance.get("discovered_by_models")) if model})
    source_domains = {
        str(domain)
        for domain in _ensure_list(provenance.get("source_domains"))
        if str(domain).strip()
    }
    for url_key in ("source_url", "official_event_page", "official_website", "registration_url", "submission_url"):
        domain = _hostname(event.get(url_key))
        if domain:
            source_domains.add(domain)
    search_metadata = event.get("search_metadata") if isinstance(event.get("search_metadata"), dict) else {}
    query_count = int(provenance.get("discovery_query_count", 0) or 0)
    query_count = max(query_count, len(_ensure_list(search_metadata.get("search_queries_used"))))
    provenance["discovered_by_models"] = models
    provenance["source_domains"] = sorted(source_domains)
    provenance["discovery_query_count"] = query_count
    provenance["consensus_strength"] = consensus_strength(models)
    event["discovery_provenance"] = provenance
    return event


def _ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple | set):
        return list(value)
    return [value]


def _hostname(url: Any) -> str:
    if not url:
        return ""
    text = str(url).strip()
    if not text:
        return ""
    if not re.match(r"^https?://", text, flags=re.I):
        text = "https://" + text.lstrip("/")
    return urlparse(text).netloc.lower().removeprefix("www.")


def _domains_in_text(text: str) -> set[str]:
    domains = set()
    for match in re.finditer(
        r"(?:site:)?([a-z0-9.-]+\.(?:gov\.in|nic\.in|ac\.in|edu\.in|org\.in|org|in|com))",
        text,
        flags=re.I,
    ):
        domain = match.group(1).lower().removeprefix("www.")
        if "." in domain:
            domains.add(domain)
    return domains


def _compact_model_name(model: Any) -> str:
    text = str(model or "").strip()
    aliases = {
        "opencode/big-pickle": "big-pickle",
        "opencode/hy3-preview-free": "hy3",
        "opencode/minimax-m2.5-free": "minimax",
        "cloudflare-workers-ai/@cf/moonshotai/kimi-k2.6": "kimi",
    }
    return aliases.get(text, text)
