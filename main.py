#!/usr/bin/env python3
"""
Indian Government Hackathon Intelligence Engine
11-Patch Production-Grade Enhancement
Preserves existing architecture, adds enterprise-grade reliability
"""

from opencode_ai import Opencode
import json
import sys
import time
import re
import os
from pathlib import Path
from datetime import datetime, date, timedelta
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

from coverage_intelligence import CoverageIntelligenceEngine, SearchSaturationTracker, attach_consensus_to_event


# ==================================================
# PATCH 1 — STALE EVENT DETECTION
# ==================================================
STALE_KEYWORDS = [
    "applications closed",
    "registration closed",
    "submissions closed",
    "deadline passed",
    "winners announced",
    "finalists announced",
    "shortlisted teams",
    "evaluation ongoing",
    "judging in progress",
    "hackathon completed",
    "event concluded",
    "submission period ended",
    "registration deadline",
    "last date",
    "apply by",
    "closed on",
    "expired",
    "no longer accepting",
]


def check_event_is_stale(text: str) -> bool:
    """PATCH 1: Detect stale events from text content"""
    if not text:
        return False
    normalized = re.sub(r'\s+', ' ', text.lower())
    for keyword in STALE_KEYWORDS:
        if keyword.lower() in normalized:
            return True
    return False


# ==================================================
# PATCH 2 — NON-TECHNICAL FILTER
# ==================================================
NON_TECHNICAL_KEYWORDS = [
    "essay", "slogan", "mascot", "logo contest", "poster competition",
    "photography contest", "debate", "poetry", "quiz competition",
    "drawing contest", "awareness campaign", "art competition",
    "content writing", "blog writing", "video contest", "short film",
    "music competition", "dance competition", "cultural event"
]


TECHNICAL_KEYWORDS = [
    "coding", "prototype", "ai", "ml", "machine learning", "deep learning",
    "engineering", "innovation", "startup", "software", "hardware",
    "cybersecurity", "data", "cloud", "blockchain", "iot", "robotics",
    "automation", "digital", "tech", "research", "development",
    "algorithm", "hacking", "hackathon", "challenge"
]


def is_non_technical(title: str, text: str = "") -> bool:
    """PATCH 2: Detect and exclude non-technical opportunities"""
    combined = f"{title} {text}".lower()
    non_tech_found = any(kw in combined for kw in NON_TECHNICAL_KEYWORDS)
    tech_found = any(kw in combined for kw in TECHNICAL_KEYWORDS)
    return non_tech_found and not tech_found


# ==================================================
# PATCH 3 — DUPLICATE SUPPRESSION ENGINE
# ==================================================
def normalize_title(title: str) -> str:
    """PATCH 3a: Normalize title for deduplication"""
    if not title:
        return ""
    normalized = title.lower()
    normalized = re.sub(r'[^\w\s]', '', normalized)
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    normalized = re.sub(r'\s*20\d{2}\s*', '', normalized)
    normalized = re.sub(r'\s*202\d\s*', '', normalized)
    return normalized


def canonicalize_url(url: str) -> str:
    """PATCH 3b: Canonicalize URL for deduplication"""
    if not url:
        return ""
    try:
        parsed = urlparse(url.lower())
        path = parsed.path.rstrip('/')
        query = parse_qs(parsed.query)
        filtered_query = {k: v for k, v in query.items() 
                         if k not in ['utm_', 'fbclid', 'gclid', 'ref']}
        return urlunparse((
            parsed.scheme,
            parsed.netloc,
            path,
            parsed.params,
            urlencode(filtered_query, doseq=True),
            ''
        ))
    except:
        return url.lower().rstrip('/')


def is_duplicate(candidate, existing_candidates) -> bool:
    """PATCH 3c: Determine if candidate is duplicate"""
    cand_url = canonicalize_url(candidate.get("registration_url", ""))
    cand_title = normalize_title(candidate.get("hackathon_name", ""))
    cand_organizer = candidate.get("hosting_organization", "").lower()
    
    for existing in existing_candidates:
        exist_url = canonicalize_url(existing.get("registration_url", ""))
        exist_title = normalize_title(existing.get("hackathon_name", ""))
        exist_organizer = existing.get("hosting_organization", "").lower()
        
        if cand_url and exist_url and cand_url == exist_url:
            return True
        if cand_title and exist_title and cand_title == exist_title:
            return True
        if cand_organizer and exist_organizer and cand_organizer == exist_organizer:
            deadline_match = (candidate.get("application_deadline") == 
                           existing.get("application_deadline"))
            if deadline_match:
                return True
    return False


# ==================================================
# PATCH 4 — CACHE TTL VALIDATION
# ==================================================
CACHE_DEFAULT_TTL_HOURS = 24
CACHE_FORCE_REVALIDATE_DAYS = 14


class CacheManager:
    """PATCH 4: Cache TTL validation with automatic invalidation"""
    def __init__(self, cache_dir=".opportunity_cache"):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
    
    def get_cache_path(self, key):
        return os.path.join(self.cache_dir, f"{key}.cache.json")
    
    def is_cache_valid(self, cache_file) -> bool:
        if not os.path.exists(cache_file):
            return False
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(cache_file))
            age = datetime.now() - mtime
            if age.total_seconds() > CACHE_DEFAULT_TTL_HOURS * 3600:
                return False
            
            with open(cache_file, 'r') as f:
                data = json.load(f)
                deadline = data.get("metadata", {}).get("search_date", "")
                if deadline:
                    search_date = datetime.strptime(deadline.split('T')[0], "%Y-%m-%d")
                    days_until = (search_date.date() - date.today()).days
                    if days_until < CACHE_FORCE_REVALIDATE_DAYS:
                        return False
            return True
        except:
            return False
    
    def get_cached(self, key):
        cache_file = self.get_cache_path(key)
        if self.is_cache_valid(cache_file):
            with open(cache_file, 'r') as f:
                return json.load(f)
        return None
    
    def save_cache(self, key, data):
        cache_file = self.get_cache_path(key)
        with open(cache_file, 'w') as f:
            json.dump(data, f)


# ==================================================
# PATCH 5 — DOMAIN CLUSTER SATURATION
# ==================================================
DOMAIN_CLUSTERS = {
    "central-government-portals": ["meity.gov.in", "indiaai.gov.in", "mygov.in", 
                                   "startupindia.gov.in", "digitalindia.gov.in"],
    "defence-space-strategic-tech": ["idex.gov.in", "drdo.gov.in", "isro.gov.in",
                                     "dste.gov.in", "inoritz.gov.in"],
    "ai-data-digital-public-infra": ["nha.gov.in", "uidai.gov.in", "nic.in",
                                     "digitallocker.gov.in", "data.gov.in"],
    "bio-health-science-rnd": ["dbtindia.gov.in", "birac.nic.in", "dst.gov.in",
                              "icmr.nic.in", "csir.nic.in"],
    "startup-msme-innovation": ["msme.gov.in", "startupindia.gov.in", "sidbi.gov.in",
                               "niti.gov.in", "pfrda.org.in"],
    "state-government-ecosystems": ["startuptn.in", "keralastartupmission.org",
                                    "startup.karnataka.gov.in", "startup.assam.gov.in"],
    "academic-government-collaborations": ["iitm.ac.in", "iitd.ac.in", "iitb.ac.in",
                                           "nits.ac.in", "iisc.ac.in"]
}


class ClusterSaturation:
    """PATCH 5: Bounded search with saturation detection"""
    def __init__(self):
        self.cluster_search_count = {}
        self.cluster_discovered = {}
    
    def is_saturated(self, cluster) -> bool:
        search_count = self.cluster_search_count.get(cluster, 0)
        discovered_count = len(self.cluster_discovered.get(cluster, set()))
        if search_count > 0 and discovered_count > 0:
            ratio = discovered_count / search_count
            if ratio < 0.3:
                return True
        return search_count >= 10
    
    def record_search(self, cluster):
        self.cluster_search_count[cluster] = self.cluster_search_count.get(cluster, 0) + 1
    
    def record_discovery(self, cluster, url):
        if cluster not in self.cluster_discovered:
            self.cluster_discovered[cluster] = set()
        self.cluster_discovered[cluster].add(url)


# ==================================================
# PATCH 6 — JSON RELIABILITY LAYER
# ==================================================
def ensure_json_output(payload) -> dict:
    """PATCH 6: Guarantee valid JSON output"""
    if isinstance(payload, dict):
        return payload
    
    if isinstance(payload, str):
        try:
            json_match = re.search(r'\{[\s\S]*\}', payload)
            if json_match:
                return json.loads(json_match.group(0))
        except:
            pass
    
    return {
        "government_hackathons": [],
        "metadata": {
            "search_date": date.today().isoformat(),
            "total_active_hackathons": 0,
            "sources_scanned": [],
            "fatal_error": "JSON parsing failed - returned empty result",
            "validation_rules_applied": ["json_reliability"]
        }
    }


# ==================================================
# PATCH 7 — PARTIAL FAILURE RECOVERY
# ==================================================
class PartialFailureRecovery:
    """PATCH 7: Continue on partial failures"""
    def __init__(self):
        self.surviving_results = []
        self.failed_clusters = []
        self.errors = []
    
    def add_surviving(self, cluster, events):
        self.surviving_results.append({"cluster": cluster, "events": events})
    
    def add_failure(self, cluster, error):
        self.failed_clusters.append(cluster)
        self.errors.append({"cluster": cluster, "error": str(error)})
    
    def get_final_result(self):
        all_events = []
        for result in self.surviving_results:
            all_events.extend(result["events"])
        
        return {
            "government_hackathons": all_events,
            "metadata": {
                "search_date": date.today().isoformat(),
                "total_active_hackathons": len(all_events),
                "partial_failure_recovery": True,
                "failed_clusters": self.failed_clusters,
                "errors": self.errors,
                "surviving_clusters": len(self.surviving_results)
            }
        }


# ==================================================
# PATCH 8 — WORKFLOW VALIDATION
# ==================================================
PORTAL_BLOCK_KEYWORDS = [
    "quota full", "submissions disabled", "registration disabled",
    "applications are closed", "no longer accepting applications",
    "registration closed", "submissions closed", "full",
    "waitlist only", "invitation only"
]


def registration_flow_is_active(text: str) -> bool:
    """PATCH 8: Verify registration workflow is actually active"""
    if not text:
        return False
    
    normalized = text.lower()
    for keyword in PORTAL_BLOCK_KEYWORDS:
        if keyword in normalized:
            return False
    
    active_keywords = ["apply now", "register now", "submit now", 
                      "registration open", "accepting applications",
                      "submit your", "apply now"]
    return any(kw in normalized for kw in active_keywords)


# ==================================================
# PATCH 9 — SOURCE TRUST SCORING
# ==================================================
SOURCE_PRIORITY = {
    "gov.in": 1.0,
    "nic.in": 1.0,
    "ac.in": 0.9,
    "mygov": 0.95,
    "startupindia": 0.95,
    "indiaai": 0.95,
    "aicte": 0.9,
    "niti": 0.85,
    "aggregator": 0.4,
    "devfolio": 0.5,
    "unstop": 0.5,
    "hack2skill": 0.5,
}


def calculate_confidence(event: dict) -> int:
    """PATCH 9: Deterministic source confidence scoring"""
    source_url = event.get("source_url", "").lower()
    
    source_score = 0.5
    for source, score in SOURCE_PRIORITY.items():
        if source in source_url:
            source_score = score
            break
    
    registration_verified = event.get("registration_verified", False)
    deadline_verified = event.get("deadline_verified", False)
    workflow_active = event.get("workflow_active", False)
    
    base_score = source_score * 100
    
    if registration_verified:
        base_score += 10
    if deadline_verified:
        base_score += 10
    if workflow_active:
        base_score += 5
    
    return min(100, int(base_score))


# ==================================================
# PATCH 10 — EXECUTION STABILITY
# ==================================================
def serialize_immediately(events: list, output_file: str = "hackathons_results.json"):
    """PATCH 10: Immediately serialize verified candidates"""
    for event in events:
        if isinstance(event, dict):
            attach_consensus_to_event(event)
            event.setdefault(
                "classification_tier",
                "fully_verified" if event.get("confidence_score", 0) >= 90 else "likely_active",
            )
            event.setdefault("admin_review_recommended", 60 <= event.get("confidence_score", 0) <= 89)
            event.setdefault("human_verification_needed", event["classification_tier"] == "likely_active")
            event.setdefault("blue_tick_eligible", event["classification_tier"] == "fully_verified")
    fully_verified = [event for event in events if event.get("classification_tier") == "fully_verified"]
    likely_active = [event for event in events if event.get("classification_tier") == "likely_active"]
    result = {
        "government_hackathons": events,
        "fully_verified_opportunities": fully_verified,
        "likely_active_opportunities": likely_active,
        "borderline_opportunities": [],
        "archived_opportunities": [],
        "ecosystem_opportunities": [],
        "excluded_opportunities": [],
        "metadata": {
            "search_date": date.today().isoformat(),
            "total_active_hackathons": len(events),
            "total_fully_verified": len(fully_verified),
            "total_likely_active": len(likely_active),
            "total_borderline_opportunities": 0,
            "total_archived_opportunities": 0,
            "total_ecosystem_opportunities": 0,
            "total_excluded": 0,
            "classification_tiers": {
                "fully_verified": len(fully_verified),
                "likely_active": len(likely_active),
                "borderline": 0,
                "archived": 0,
                "rejected": 0,
            },
            "serialization_time": datetime.now().isoformat(),
            "execution_mode": "immediate_serialization",
            "recall_mode": "high_recall_tiered",
        }
    }
    attach_coverage_analysis(result, queries=["legacy_immediate_serialization"], models_used=["hy3-preview"])
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    return result


def attach_coverage_analysis(result: dict, queries: list[str] | None = None, models_used: list[str] | None = None) -> dict:
    """Attach additive coverage-confidence metadata without changing event validity scores."""
    metadata = result.setdefault("metadata", {})
    active_count = len(result.get("government_hackathons", []) or [])
    rejected_count = metadata.get("total_excluded", 0)
    saturation_tracker = SearchSaturationTracker()
    saturation_tracker.record_query_batch(
        queries=queries or metadata.get("search_queries_used") or ["legacy_generation"],
        new_events_discovered=active_count,
        duplicate_events_discovered=0,
        rejected_events_discovered=rejected_count if isinstance(rejected_count, int) else 0,
    )
    coverage_engine = CoverageIntelligenceEngine(Path("coverage_history.json"))
    coverage_analysis = coverage_engine.analyze(
        final_payload=result,
        discovery_tasks=[],
        payloads=[result],
        artifacts=[],
        saturation_tracker=saturation_tracker,
        models_attempted=models_used or [],
    )
    metadata["coverage_analysis"] = coverage_analysis
    metadata["coverage_confidence"] = coverage_analysis["coverage_confidence"]
    metadata["coverage_status"] = coverage_analysis["coverage_status"]
    return result


# ==================================================
# PATCH 11 — FINAL VALIDATION GATE
# ==================================================
def final_validation_gate(events: list) -> list:
    """PATCH 11 + Government Authenticity: Revalidate every event before export"""
    authenticity_engine = GovernmentAuthenticityEngine()
    validated = []
    
    for event in events:
        if not event.get("is_open_for_new_registration", True):
            continue
        
        if event.get("is_stale", False):
            continue
        
        if event.get("current_status") in ["finalist", "judging", "archived", "completed"]:
            continue
        
        if event.get("is_duplicate", False):
            continue
        
        if event.get("is_non_technical", False):
            continue
        
        if not event.get("workflow_active", True):
            continue
        
        event = authenticity_engine.enrich_event(event)
        
        if not event.get("is_government_affiliated", False):
            continue
        
        auth = event.get("government_authenticity", {})
        if auth.get("rejection_reason"):
            continue
        
        event["confidence_score"] = calculate_confidence(event)
        
        if event.get("confidence_score", 0) < 50:
            continue
        
        validated.append(event)
    
    return validated


# ==================================================
# MAIN ENGINE CLASS (Preserves Existing Architecture)
# ==================================================
class GovernmentHackathonEngine:
    def __init__(self):
        self.cache = CacheManager()
        self.saturation = ClusterSaturation()
        self.recovery = PartialFailureRecovery()
    
    def load_system_prompt(self):
        with open("systemprompt.md", "r") as f:
            return f.read()
    
    def check_opencode_server(self):
        try:
            client = Opencode()
            print("Connecting to OpenCode local server at http://localhost:54321...")
            return client
        except Exception as e:
            print(f"Cannot connect to OpenCode server: {str(e)}")
            sys.exit(1)
    
    def search_with_cluster_approach(self, client, system_prompt, query):
        """Search using domain clusters with saturation control"""
        results = []
        
        for cluster_name, domains in DOMAIN_CLUSTERS.items():
            if self.saturation.is_saturated(cluster_name):
                print(f"Cluster '{cluster_name}' saturated - skipping")
                continue
            
            self.saturation.record_search(cluster_name)
            
            try:
                cluster_query = f"{query}\n\nFocus on: {', '.join(domains)}"
                
                session = client.session.create()
                parts = [{"type": "text", "text": cluster_query}]
                
                response = client.session.chat(
                    id=session.id,
                    model_id="hy3-preview",
                    provider_id="opencode",
                    parts=parts,
                    system=system_prompt,
                    mode="search",
                    tools={"web_search": True}
                )
                
                response_text = response.content if hasattr(response, 'content') else str(response)
                cluster_events = ensure_json_output(response_text).get("government_hackathons", [])
                
                for event in cluster_events:
                    self.saturation.record_discovery(cluster_name, 
                        canonicalize_url(event.get("registration_url", "")))
                
                self.recovery.add_surviving(cluster_name, cluster_events)
                client.session.delete(session.id)
                
            except Exception as e:
                self.recovery.add_failure(cluster_name, e)
                continue
        
        return self.recovery.get_final_result()
    
    def process_events(self, raw_result):
        events = raw_result.get("government_hackathons", [])
        
        unique_events = []
        for event in events:
            if not is_duplicate(event, unique_events):
                
                event_text = f"{event.get('hackathon_name', '')} {event.get('theme', '')}"
                
                if check_event_is_stale(event_text):
                    event["is_stale"] = True
                if is_non_technical(event.get("hackathon_name", ""), event_text):
                    event["is_non_technical"] = True
                
                if not event.get("registration_verified"):
                    event["registration_verified"] = registration_flow_is_active(
                        event.get("source_url", ""))
                
                unique_events.append(event)
        
        validated_events = final_validation_gate(unique_events)
        for event in validated_events:
            attach_consensus_to_event(event)
            event.setdefault(
                "classification_tier",
                "fully_verified" if event.get("confidence_score", 0) >= 90 else "likely_active",
            )
            event.setdefault("admin_review_recommended", 60 <= event.get("confidence_score", 0) <= 89)
            event.setdefault("human_verification_needed", event["classification_tier"] == "likely_active")
            event.setdefault("blue_tick_eligible", event["classification_tier"] == "fully_verified")
        fully_verified = [event for event in validated_events if event.get("classification_tier") == "fully_verified"]
        likely_active = [event for event in validated_events if event.get("classification_tier") == "likely_active"]
        
        result = {
            "government_hackathons": validated_events,
            "fully_verified_opportunities": fully_verified,
            "likely_active_opportunities": likely_active,
            "borderline_opportunities": [],
            "archived_opportunities": [],
            "ecosystem_opportunities": [],
            "excluded_opportunities": [],
            "metadata": {
                "search_date": date.today().isoformat(),
                "total_active_hackathons": len(validated_events),
                "total_fully_verified": len(fully_verified),
                "total_likely_active": len(likely_active),
                "total_borderline_opportunities": 0,
                "total_archived_opportunities": 0,
                "total_ecosystem_opportunities": 0,
                "total_excluded": 0,
                "classification_tiers": {
                    "fully_verified": len(fully_verified),
                    "likely_active": len(likely_active),
                    "borderline": 0,
                    "archived": 0,
                    "rejected": 0,
                },
                "recall_mode": "high_recall_tiered",
                "validation_rules_applied": [
                    "stale_detection", "non_technical_filter", "duplicate_suppression",
                    "cache_ttl", "cluster_saturation", "json_reliability",
                    "partial_failure_recovery", "workflow_validation",
                    "source_trust_scoring", "execution_stability", "final_validation"
                ]
            }
        }
        attach_coverage_analysis(
            result,
            queries=[f"legacy_cluster:{cluster}" for cluster in DOMAIN_CLUSTERS],
            models_used=["hy3-preview"],
        )
        with open("hackathons_results.json", "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        return result


def main():
    print("\n" + "="*70)
    print("GOVERNMENT HACKATHON INTELLIGENCE ENGINE")
    print("11-Patch Production-Grade Enhancement Active")
    print("="*70 + "\n")
    
    engine = GovernmentHackathonEngine()
    
    client = engine.check_opencode_server()
    system_prompt = engine.load_system_prompt()
    
    query = """Search for ACTIVE Indian government hackathons with OPEN REGISTRATIONS.
    STRICT RULES:
    1. ONLY include events where registrations ARE CURRENTLY OPEN
    2. EXCLUDE events with closed deadlines or stale status
    3. EXCLUDE non-technical contests (essay/logo/poster/quiz/debate/poetry)
    4. Use DOMAIN CLUSTERS for bounded search
    5. Return VALID JSON ONLY
    """
    
    try:
        raw_result = engine.search_with_cluster_approach(client, system_prompt, query)
        final_result = engine.process_events(raw_result)
        
        print(json.dumps(final_result, indent=2, ensure_ascii=False))
        
        metadata = final_result.get("metadata", {})
        print("\n" + "="*70)
        print("EXECUTION SUMMARY:")
        print(f"   Active Hackathons: {metadata.get('total_active_hackathons', 0)}")
        print(f"   Search Date: {metadata.get('search_date', 'N/A')}")
        print(f"   Rules Applied: {len(metadata.get('validation_rules_applied', []))}")
        
        if metadata.get("partial_failure_recovery"):
            print(f"   Partial Recovery: {metadata.get('surviving_clusters', 0)} clusters survived")
            print(f"   Failed Clusters: {len(metadata.get('failed_clusters', []))}")
        
        print("="*70 + "\n")
        
    except Exception as e:
        fallback = {
            "government_hackathons": [],
            "metadata": {
                "search_date": date.today().isoformat(),
                "total_active_hackathons": 0,
                "error": str(e),
                "fallback": "Partial failure recovery - returning empty result"
            }
        }
        print(json.dumps(fallback, indent=2))
        serialize_immediately([])


# ==================================================
# GOVERNMENT AUTHENTICITY INFERENCE ENGINE
# Multi-Signal Trust Scoring (Replaces Simplistic Domain Validation)
# ==================================================

from dataclasses import dataclass
from typing import List, Optional


# ==================================================
# PATCH 10 — EXPLICIT EVIDENCE STRUCTURE
# ==================================================
@dataclass
class EventEvidence:
    """Structured evidence for trust signal computation"""
    metadata_text: str = ""
    organizer_text: str = ""
    title_text: str = ""
    thematic_text: str = ""
    source_url: str = ""
    ownership_text: str = ""
    workflow_text: str = ""
    source_references: List[str] = None
    
    def __post_init__(self):
        if self.source_references is None:
            self.source_references = []


# ==================================================
# HARDENED GOVERNMENT AUTHENTICITY ENGINE
# Calibrated Multi-Signal Trust Inference
# ==================================================
class GovernmentAuthenticityEngine:
    """
    PATCHED: Hardened Government Authenticity Inference Engine
    
    Calibrated multi-signal trust scoring with:
    - Normalized weighted formula
    - Reduced penalties
    - Source diversity signal
    - Free-host scam detection
    - Tracking URL penalties
    - Proper borderline handling
    """
    
    def __init__(self):
        self.domain_signals = {
            ".gov.in": 0.50,
            ".nic.in": 0.50,
            ".ac.in": 0.20,
            ".org.in": 0.08,
            ".edu.in": 0.15,
            ".ai": 0.05,
            ".io": 0.02,
            ".com": 0.00,
        }
        
        self.entity_keywords = [
            "government of india", "ministry of", "ministry",
            "department of", "govt of", "government initiative",
            "statutory body", "autonomous body", "national mission",
            "public sector", "psu", "department", "state government",
            "official portal", "government-backed", "governement",
            "dpiit", "digital india", "startup india", "mygov",
            "indiaai", "niti aayog", "aicte", "nic", "meity"
        ]
        
        self.known_portals = [
            "mygov.in", "indiaai.gov.in", "startupindia.gov.in",
            "meity.gov.in", "aicte-india.org", "niti.gov.in",
            "digitalindia.gov.in", "innovateindia.mygov.in",
            "sih.gov.in", "idex.gov.in", "drdo.gov.in", "isro.gov.in",
            "nha.gov.in", "uidai.gov.in", "pfrda.org.in",
            "startuptn.in", "keralastartupmission.org", "startup.karnataka.gov.in"
        ]
        
        self.negative_keywords = [
            "parking page", "coming soon", "under construction",
            "best online casino", "adult", "gambling", "lottery scam",
            "click here to win", "act now!!", "limited time offer",
            "congratulations winner", "claim your prize now",
            "free gift", "make money fast", "work from home",
            "crypto", "bitcoin", "investment scheme", "₹"
        ]
        
        self.govt_language_patterns = [
            "guidelines", "notification", "official memorandum",
            "scheme", "challenge statement", "policy mission",
            "autonomous institution", "cabinet", "statutory",
            "circular", "press release", " PIB ", "vigilance",
            "recruitment", "notification no", "order no"
        ]
        
        self.free_hosts = [
            "blogspot", "wix", "weebly", "wordpress",
            "sites.google", "notion.site", "github.io"
        ]
        
        self.tracking_patterns = [
            "utm_", "aff=", "ref=", "refsrc", "fbclid", "gclid",
            "bit.ly", "tinyurl", "cutt.ly", "t.co", "ow.ly"
        ]
    
    def extract_evidence(self, event: dict) -> EventEvidence:
        """PATCH 10: Extract structured evidence from event"""
        return EventEvidence(
            metadata_text=event.get("description", "") or "",
            organizer_text=event.get("hosting_organization", "") or event.get("organizer", ""),
            title_text=event.get("hackathon_name", "") or event.get("title", ""),
            thematic_text=event.get("theme", ""),
            source_url=event.get("source_url", "") or event.get("registration_url", ""),
            ownership_text=event.get("about", "") or "",
            workflow_text=event.get("eligibility_criteria", ""),
            source_references=event.get("source_urls", [])
        )
    
    def compute_domain_signal(self, url: str) -> float:
        """Signal 1: Domain type scoring - UNCHANGED"""
        if not url:
            return 0.0
        url_lower = url.lower()
        for domain, score in self.domain_signals.items():
            if domain in url_lower:
                return score
        return 0.0
    
    def compute_entity_signal(self, evidence: EventEvidence) -> float:
        """Signal 2: Government entity detection"""
        text = f"{evidence.organizer_text} {evidence.metadata_text}".lower()
        if not text:
            return 0.0
        
        score = 0.0
        for keyword in self.entity_keywords:
            if keyword in text:
                score += 0.20
                if score >= 0.60:
                    break
        
        return min(0.60, score)
    
    def compute_ownership_signal(self, evidence: EventEvidence) -> float:
        """PATCH 4: Renamed from compute_footer_signal"""
        text = f"{evidence.ownership_text} {evidence.organizer_text}".lower()
        if not text:
            return 0.0
        
        ownership_indicators = [
            "government of india", "official website", "ministry of",
            "national informatics centre", "nic", "govt of india",
            "government of ", "powered by nic", "hosted by"
        ]
        
        score = 0.0
        for indicator in ownership_indicators:
            if indicator in text:
                score += 0.15
        
        return min(0.30, score)
    
    def compute_known_portal_signal(self, url: str) -> float:
        """PATCH 1: Renamed from compute_cross_portal_signal
        PATCH 9: Now checks if hosted on known portal, not fake backlinks"""
        if not url:
            return 0.0
        
        url_lower = url.lower()
        portal_count = sum(1 for portal in self.known_portals if portal in url_lower)
        
        if portal_count >= 1:
            return 0.35
        return 0.0
    
    def compute_source_diversity_signal(self, evidence: EventEvidence) -> float:
        """PATCH 7: Add source diversity signal"""
        sources = evidence.source_references or []
        
        if len(sources) >= 3:
            return 0.40
        elif len(sources) == 2:
            return 0.25
        elif len(sources) == 1:
            return 0.10
        return 0.0
    
    def compute_negative_signal(self, evidence: EventEvidence) -> float:
        """PATCH 2: Reduced over-aggressive penalties
        PATCH 8: Added free-host detection
        PATCH 9: Added tracking URL penalties"""
        text = f"{evidence.metadata_text} {evidence.title_text} {evidence.thematic_text}".lower()
        url = evidence.source_url.lower()
        
        penalty = 0.0
        
        if "parking" in text:
            penalty += 0.60
        
        if any(kw in text for kw in self.negative_keywords):
            penalty += 0.30
        
        has_govt_language = any(kw in text for kw in self.entity_keywords)
        if not has_govt_language and len(text) > 100:
            penalty += 0.15
        
        if any(host in url for host in self.free_hosts):
            penalty += 0.40
        
        if any(pattern in url for pattern in self.tracking_patterns):
            penalty += 0.20
        
        return min(1.0, penalty)
    
    def compute_semantic_signal(self, evidence: EventEvidence) -> float:
        """PATCH 3: CAP semantic contribution at 0.15"""
        text = f"{evidence.metadata_text} {evidence.thematic_text}".lower()
        if not text:
            return 0.0
        
        score = 0.0
        for pattern in self.govt_language_patterns:
            if pattern in text:
                score += 0.15
                if score >= 0.15:
                    break
        
        return min(0.15, score)
    
    def compute_trust_score(self, event: dict) -> float:
        """PATCH 5: Normalized weighted trust scoring
        
        Formula:
        trust = (domain*0.30 + entity*0.25 + ownership*0.15 + 
                portal*0.20 + semantic*0.10 + diversity*0.10) - negative
        """
        evidence = self.extract_evidence(event)
        
        domain = self.compute_domain_signal(evidence.source_url)
        entity = self.compute_entity_signal(evidence)
        ownership = self.compute_ownership_signal(evidence)
        portal = self.compute_known_portal_signal(evidence.source_url)
        semantic = self.compute_semantic_signal(evidence)
        diversity = self.compute_source_diversity_signal(evidence)
        negative = self.compute_negative_signal(evidence)
        
        weighted_sum = (
            domain * 0.30 +
            entity * 0.25 +
            ownership * 0.15 +
            portal * 0.20 +
            semantic * 0.10 +
            diversity * 0.10
        )
        
        trust_score = weighted_sum - negative
        
        return max(0.0, min(1.0, trust_score))
    
    def evaluate_event(self, event: dict) -> dict:
        """PATCH 11: Signal explainability
        PATCH 12: Trust decision states
        PATCH 6: Fix borderline contamination"""
        evidence = self.extract_evidence(event)
        
        trust_score = self.compute_trust_score(event)
        
        domain = self.compute_domain_signal(evidence.source_url)
        entity = self.compute_entity_signal(evidence)
        ownership = self.compute_ownership_signal(evidence)
        portal = self.compute_known_portal_signal(evidence.source_url)
        semantic = self.compute_semantic_signal(evidence)
        diversity = self.compute_source_diversity_signal(evidence)
        negative = self.compute_negative_signal(evidence)
        
        if trust_score >= 0.75:
            government_verified = True
            borderline = False
            decision = "verified"
        elif trust_score >= 0.50:
            government_verified = False
            borderline = True
            decision = "borderline"
        else:
            government_verified = False
            borderline = False
            decision = "rejected"
        
        return {
            "government_authenticity": {
                "trust_score": round(trust_score, 2),
                "signal_breakdown": {
                    "domain": round(domain, 2),
                    "entity": round(entity, 2),
                    "ownership": round(ownership, 2),
                    "portal": round(portal, 2),
                    "semantic": round(semantic, 2),
                    "source_diversity": round(diversity, 2),
                    "negative": round(-negative, 2)
                },
                "government_verified": government_verified,
                "borderline_government_affiliation": borderline,
                "trust_decision": decision,
                "rejection_reason": self._get_rejection_reason(trust_score) if trust_score < 0.50 else None
            },
            "is_government_affiliated": government_verified
        }
    
    def _get_rejection_reason(self, trust_score: float) -> str:
        if trust_score < 0.25:
            return "insufficient_trust_signals"
        elif trust_score < 0.40:
            return "no_government_affiliation_detected"
        elif trust_score < 0.50:
            return "negative_signals_present"
        return "trust_score_below_threshold"
    
    def enrich_event(self, event: dict) -> dict:
        """PATCH 6: Only verified events get is_government_affiliated=true"""
        auth_result = self.evaluate_event(event)
        event["government_authenticity"] = auth_result["government_authenticity"]
        event["is_government_affiliated"] = auth_result["is_government_affiliated"]
        return event
    
    def filter_events(self, events: list) -> tuple:
        """PATCH 13: Proper separation of verified/borderline/rejected"""
        verified = []
        borderline = []
        rejected = []
        
        for event in events:
            auth = self.evaluate_event(event)
            event["government_authenticity"] = auth["government_authenticity"]
            event["is_government_affiliated"] = auth["is_government_affiliated"]
            
            decision = auth["government_authenticity"]["trust_decision"]
            if decision == "verified":
                verified.append(event)
            elif decision == "borderline":
                borderline.append(event)
            else:
                rejected.append(event)
        
        return verified, borderline, rejected


# ==================================================
# INTEGRATION: Authenticity Engine in Pipeline
# ==================================================

def apply_authenticity_engine(events: list) -> dict:
    """Apply multi-signal trust scoring to all events"""
    engine = GovernmentAuthenticityEngine()
    verified, borderline, rejected = engine.filter_events(events)
    
    return {
        "verified_government": verified,
        "borderline_government": borderline,
        "rejected": rejected,
        "summary": {
            "total_processed": len(events),
            "verified_count": len(verified),
            "borderline_count": len(borderline),
            "rejected_count": len(rejected)
        }
    }


if __name__ == "__main__":
    main()
