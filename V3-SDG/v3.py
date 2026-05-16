import os
import pty
import select
import subprocess
import json
import argparse
import time
import hashlib
import re
from pathlib import Path
from datetime import datetime, date

MODEL = "opencode/big-pickle"
SYSTEM_PROMPT_FILE = "systemprompt.md"
TOTAL_ROUNDS = 3

ACTIVE_STATUSES = {
    "proposal_open",
    "grant_open",
    "research_open",
    "active",
    "rolling",
    "recurring",
    "call_open",
    "funding_open",
    "innovation_open",
    "accepting_applications",
}

REJECTED_STATUSES = {
    "expired",
    "archived",
    "completed",
    "applications_closed",
    "closed",
}

ROLLING_DEADLINE_TYPES = {
    "rolling",
    "rolling_applications",
    "always_open",
    "recurring",
    "continuous_intake",
}

# Mandatory domain coverage for diversified discovery
GOVERNMENT_DOMAINS = [
    "dst.gov.in",
    "serbonline.in",
    "icmr.gov.in",
    "dbtindia.gov.in",
    "birac.nic.in",
    "csir.res.in",
    "ugc.gov.in",
    "aicte-india.org",
    "icssr.org",
    "niti.gov.in",
    "startupindia.gov.in",
    "atalinnovationmission.gov.in",
    "aim.gov.in",
    "stpi.in",
    "moef.gov.in",
    "jalshakti.gov.in",
    "mnre.gov.in",
    "msde.gov.in",
    "msme.gov.in",
]

INTERNATIONAL_DOMAINS = [
    "undp.org",
    "unicef.org",
    "unesco.org",
    "worldbank.org",
    "adb.org",
    "giz.de",
    "ukri.org",
    "grandchallenges.org",
    "gatesfoundation.org",
    "wellcome.org",
    "climate-kic.org",
]

CSR_DOMAINS = [
    "reliancefoundation.org",
    "infosys.org",
    "wiprofoundation.org",
    "azimpremjifoundation.org",
    "mahindrafoundation.org",
    "nasscomfoundation.org",
    "socialalpha.org",
    "villgro.org",
    "acumen.org",
]

ACADEMIC_DOMAINS = [
    "iitm.ac.in",
    "iisc.ac.in",
    "iitd.ac.in",
    "iitb.ac.in",
    "iitk.ac.in",
    "iitg.ac.in",
    "bits-pilani.ac.in",
    "du.ac.in",
]

OUTPUT_DIR = Path("round_outputs")
OUTPUT_DIR.mkdir(exist_ok=True)


def clear_previous_outputs():
    """Remove previous round outputs and the final consolidated file."""
    for file_path in OUTPUT_DIR.glob("round*_raw.json"):
        file_path.unlink(missing_ok=True)

    for file_path in OUTPUT_DIR.glob("round*_raw.txt"):
        file_path.unlink(missing_ok=True)

    for file_path in OUTPUT_DIR.glob("round*_final.json"):
        file_path.unlink(missing_ok=True)

    (OUTPUT_DIR / "final_results.json").unlink(missing_ok=True)

    Path("final_results.json").unlink(missing_ok=True)
    Path("mega_sdg_research_export.json").unlink(missing_ok=True)
    Path("final_sdg_research_funding.json").unlink(missing_ok=True)
    Path("sdg_research_ready4db.json").unlink(missing_ok=True)
    (Path("output") / "archive" / "sdg_research_ready4db.json").unlink(missing_ok=True)


def extract_json_payload(text):
    """
    Extract JSON from mixed text output supporting multiple formats:
    
    CASE 1: Pure JSON object/array
    CASE 2: Markdown fenced JSON (```json ... ```)
    CASE 3: Mixed logs + JSON somewhere in output
    CASE 4: Narrative text followed by JSON
    CASE 5: Multiple JSON objects in output
    
    Returns: Extracted data or None
    """
    if not text or not text.strip():
        print("[JSON] No text to extract from")
        return None
    
    # CASE 2: Try markdown fenced JSON first (most reliable)
    markdown_blocks = re.findall(r'```(?:json)?\s*\n(.*?)\n```', text, re.DOTALL)
    if markdown_blocks:
        for block in markdown_blocks:
            try:
                return json.loads(block)
            except json.JSONDecodeError:
                continue
    
    # CASE 1 & 3: Find first [ or { and try to extract
    brace_idx = text.find('{')
    bracket_idx = text.find('[')
    
    def try_extract_at_position(start_idx, is_array):
        """Try extracting JSON starting at position."""
        if start_idx == -1:
            return None
        
        if is_array:
            for end_idx in range(len(text), start_idx, -1):
                try:
                    candidate = text[start_idx:end_idx]
                    result = json.loads(candidate)
                    if isinstance(result, list):
                        return result
                except json.JSONDecodeError:
                    continue
        else:
            for end_idx in range(len(text), start_idx, -1):
                try:
                    candidate = text[start_idx:end_idx]
                    result = json.loads(candidate)
                    if isinstance(result, dict):
                        return result
                except json.JSONDecodeError:
                    continue
        return None
    
    # Try array first if it comes first
    if bracket_idx != -1 and (brace_idx == -1 or bracket_idx < brace_idx):
        result = try_extract_at_position(bracket_idx, True)
        if result:
            return result
    
    # Try object
    if brace_idx != -1:
        result = try_extract_at_position(brace_idx, False)
        if result:
            return result
    
    print("[JSON] No valid JSON found in output")
    return None


def compute_file_hash(file_path):
    """Compute SHA256 hash for a file."""
    try:
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception:
        return None


def load_json_from_file(file_path):
    """Load JSON from file; returns parsed data or None."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        if not content.strip():
            print(f"[JSON] Parse failed: empty file {file_path}")
            return None
        return json.loads(content)
    except Exception as e:
        print(f"[JSON] Parse failed for {file_path}: {e}")
        return None


def select_newest_json_file(candidate_paths):
    """Pick the newest valid JSON file by mtime and hash."""
    newest = None

    for path in candidate_paths:
        if not path.exists():
            continue

        data = load_json_from_file(path)
        if data is None:
            continue

        mtime = path.stat().st_mtime
        file_hash = compute_file_hash(path)

        if newest is None or mtime > newest["mtime"]:
            newest = {"path": path, "data": data, "mtime": mtime, "hash": file_hash}
        elif newest and mtime == newest["mtime"] and file_hash and file_hash != newest["hash"]:
            newest = {"path": path, "data": data, "mtime": mtime, "hash": file_hash}

    return newest


def resolve_round_json(round_num, output_text):
    """Resolve JSON for a round from files or fallback extraction."""
    candidate_paths = [
        OUTPUT_DIR / f"round{round_num}_final.json",
        OUTPUT_DIR / "final_results.json",
        Path("final_results.json"),
    ]

    newest = select_newest_json_file(candidate_paths)
    if newest:
        print(f"[JSON] Found file: {newest['path']}")
        print("[JSON] Parse success")
        return newest["data"], newest["path"]

    if output_text:
        print("[JSON] Fallback extraction used")
        data = extract_json_payload(output_text)
        if data is not None:
            print("[JSON] Parse success")
        else:
            print("[JSON] Parse failed")
        return data, None

    print("[JSON] Parse failed: no files and no output")
    return None, None


def extract_json_from_text(text):
    """Legacy wrapper for backward compatibility."""
    return extract_json_payload(text)


def normalize_text(value):
    if value is None:
        return ""
    return str(value).strip()


def coerce_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}
    if isinstance(value, (int, float)):
        return value != 0
    return False


def infer_sdg_alignment(opportunity):
    """Infer SDG alignment from research opportunity metadata."""
    text_fields = [
        normalize_text(opportunity.get("research_domain")),
        normalize_text(opportunity.get("funding_type")),
        normalize_text(opportunity.get("organization")),
        normalize_text(opportunity.get("ministry")),
        " ".join([normalize_text(x) for x in (opportunity.get("focus_areas") or [])]),
        " ".join([normalize_text(x) for x in (opportunity.get("tags") or [])]),
    ]
    blob = " ".join(text_fields).lower()

    keyword_to_sdg = {
        "health": "SDG3",
        "medical": "SDG3",
        "public health": "SDG3",
        "education": "SDG4",
        "women": "SDG5",
        "gender": "SDG5",
        "water": "SDG6",
        "sanitation": "SDG6",
        "energy": "SDG7",
        "renewable": "SDG7",
        "solar": "SDG7",
        "employment": "SDG8",
        "innovation": "SDG9",
        "industry": "SDG9",
        "infrastructure": "SDG9",
        "inequality": "SDG10",
        "urban": "SDG11",
        "cities": "SDG11",
        "consumption": "SDG12",
        "climate": "SDG13",
        "ocean": "SDG14",
        "forest": "SDG15",
        "biodiversity": "SDG15",
        "governance": "SDG16",
        "partnership": "SDG17",
        "agriculture": "SDG2",
        "agri": "SDG2",
        "food": "SDG2",
        "poverty": "SDG1",
        "rural": "SDG1",
    }

    inferred = set()
    for value in (opportunity.get("sdg_alignment") or []):
        text = normalize_text(value).upper()
        if not text:
            continue
        if text.startswith("SDG"):
            suffix = text[3:]
            if suffix.isdigit():
                inferred.add(f"SDG{int(suffix)}")
            else:
                inferred.add(text)
        elif text.isdigit():
            inferred.add(f"SDG{int(text)}")
        else:
            inferred.add(text)

    for token, sdg in keyword_to_sdg.items():
        if token in blob:
            inferred.add(sdg)

    def sdg_sort_key(value):
        match = re.match(r"^SDG(\d+)$", value.upper())
        if match:
            return (0, int(match.group(1)))
        return (1, value)

    return sorted(inferred, key=sdg_sort_key)


def build_canonical_id(opportunity):
    """Build canonical ID for deduplication."""
    existing = normalize_text(opportunity.get("canonical_id"))
    if existing:
        return existing

    parts = [
        normalize_text(opportunity.get("project_name")).lower(),
        normalize_text(opportunity.get("application_url")).lower(),
        normalize_text(opportunity.get("official_program_page")).lower(),
        normalize_text(opportunity.get("official_website")).lower(),
        normalize_text(opportunity.get("source_url")).lower(),
    ]
    source = "|".join([p for p in parts if p])
    if not source:
        return ""
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]


def confidence_score(opportunity):
    """Calculate confidence score for opportunity."""
    verification = opportunity.get("verification") or {}
    confidence = normalize_text(verification.get("confidence")).lower()
    score = {"high": 3, "medium": 2, "low": 1}.get(confidence, 0)
    if coerce_bool(verification.get("official_source")):
        score += 2
    if coerce_bool(verification.get("official_url_confirmed")):
        score += 1
    if coerce_bool(verification.get("active_confirmation")):
        score += 1
    if coerce_bool(verification.get("deadline_verified")):
        score += 1
    if coerce_bool(verification.get("aggregator_only")):
        score -= 2
    return score


def normalize_research_opportunity(raw_opportunity):
    """Normalize raw opportunity into research schema."""
    if not isinstance(raw_opportunity, dict):
        return None

    funding_support = raw_opportunity.get("funding_support") if isinstance(raw_opportunity.get("funding_support"), dict) else {}
    eligibility = raw_opportunity.get("eligibility") if isinstance(raw_opportunity.get("eligibility"), dict) else {}
    geography = raw_opportunity.get("geography") if isinstance(raw_opportunity.get("geography"), dict) else {}
    verification = raw_opportunity.get("verification") if isinstance(raw_opportunity.get("verification"), dict) else {}
    
    # Support legacy startup schema field mappings
    project_name = normalize_text(
        raw_opportunity.get("project_name") or 
        raw_opportunity.get("program_name") or 
        raw_opportunity.get("funding_program") or
        raw_opportunity.get("full_name")
    )
    
    funding_type = normalize_text(
        raw_opportunity.get("funding_type") or 
        raw_opportunity.get("program_type") or 
        raw_opportunity.get("grant_type")
    )
    
    research_domain = normalize_text(
        raw_opportunity.get("research_domain") or 
        raw_opportunity.get("sector") or 
        raw_opportunity.get("domain") or
        raw_opportunity.get("theme")
    )
    
    ministry = normalize_text(
        raw_opportunity.get("ministry") or 
        raw_opportunity.get("program_owner") or 
        raw_opportunity.get("hosting_organization")
    )

    normalized = {
        "project_name": project_name,
        "funding_program": funding_type,
        "funding_type": funding_type,
        "status": normalize_text(raw_opportunity.get("status") or raw_opportunity.get("current_status") or "active").lower(),
        "deadline_type": normalize_text(raw_opportunity.get("deadline_type")),
        "deadline": normalize_text(raw_opportunity.get("deadline") or raw_opportunity.get("application_deadline") or raw_opportunity.get("registration_close_date")),
        "deadline_iso": normalize_text(raw_opportunity.get("deadline_iso") or raw_opportunity.get("deadline") or raw_opportunity.get("application_deadline")),
        "application_url": normalize_text(raw_opportunity.get("application_url") or raw_opportunity.get("registration_url") or raw_opportunity.get("submission_url")),
        "official_website": normalize_text(raw_opportunity.get("official_website")),
        "official_program_page": normalize_text(raw_opportunity.get("official_program_page") or raw_opportunity.get("official_event_page")),
        "source_url": normalize_text(raw_opportunity.get("source_url")),
        "organization": normalize_text(raw_opportunity.get("organization") or raw_opportunity.get("hosting_organization")),
        "ministry": ministry,
        "research_domain": research_domain,
        "focus_areas": list(raw_opportunity.get("focus_areas") or []),
        "funding_support": {
            "grant_type": normalize_text(funding_support.get("grant_type") or funding_support.get("type")),
            "amount_min": funding_support.get("amount_min"),
            "amount_max": funding_support.get("amount_max"),
            "currency": normalize_text(funding_support.get("currency") or "INR") or "INR",
        },
        "eligibility": {
            "summary": normalize_text(eligibility.get("summary") or raw_opportunity.get("eligibility_summary"))
        },
        "target_entities": list(raw_opportunity.get("target_entities") or []),
        "institution_type": normalize_text(raw_opportunity.get("institution_type")),
        "research_stage": list(raw_opportunity.get("research_stage") or raw_opportunity.get("startup_stage") or []),
        "geography": {
            "country": normalize_text(geography.get("country") or "India") or "India",
            "state": normalize_text(geography.get("state")),
            "city": normalize_text(geography.get("city")),
        },
        "sdg_alignment": list(raw_opportunity.get("sdg_alignment") or []),
        "is_government": coerce_bool(raw_opportunity.get("is_government")),
        "is_private": coerce_bool(raw_opportunity.get("is_private")),
        "is_academic": coerce_bool(raw_opportunity.get("is_academic")),
        "is_corporate": coerce_bool(raw_opportunity.get("is_corporate")),
        "verification": {
            "official_source": coerce_bool(verification.get("official_source", True)),
            "official_url_confirmed": coerce_bool(verification.get("official_url_confirmed", True)),
            "deadline_verified": coerce_bool(verification.get("deadline_verified", True)),
            "active_confirmation": coerce_bool(verification.get("active_confirmation", True)),
            "aggregator_only": coerce_bool(verification.get("aggregator_only", False)),
            "confidence": normalize_text(verification.get("confidence") or "high") or "high",
        },
        "canonical_id": normalize_text(raw_opportunity.get("canonical_id")),
        "tags": list(raw_opportunity.get("tags") or []),
    }

    normalized["sdg_alignment"] = infer_sdg_alignment(normalized)
    normalized["canonical_id"] = build_canonical_id(normalized)
    return normalized


def is_program_active(opportunity):
    """Check if opportunity is active."""
    status = normalize_text(opportunity.get("status")).lower()
    if status in REJECTED_STATUSES:
        return False
    if status in ACTIVE_STATUSES:
        return True
    return status == ""


def is_deadline_valid(opportunity):
    """Check if opportunity deadline is valid."""
    status = normalize_text(opportunity.get("status")).lower()
    deadline_type = normalize_text(opportunity.get("deadline_type")).lower()
    deadline_value = normalize_text(opportunity.get("deadline_iso") or opportunity.get("deadline"))

    if deadline_type in ROLLING_DEADLINE_TYPES:
        return True
    if status in {"rolling_applications", "recurring", "always_open", "continuous_intake"}:
        return True
    if status in REJECTED_STATUSES:
        return False
    if not deadline_value:
        return status in ACTIVE_STATUSES

    try:
        if "T" in deadline_value:
            deadline = datetime.fromisoformat(deadline_value).date()
        else:
            deadline = datetime.strptime(deadline_value, "%Y-%m-%d").date()
        return deadline >= date.today()
    except (ValueError, TypeError):
        return status in ACTIVE_STATUSES


def is_valid_funding_opportunity(opportunity):
    """Check if opportunity is valid and active."""
    return is_program_active(opportunity) and is_deadline_valid(opportunity)


def dedupe_opportunities(opportunities):
    """Deduplicate opportunities by canonical_id and other identifiers."""
    deduped = []
    key_to_index = {}

    def key_variants(opportunity):
        return [
            ("canonical_id", normalize_text(opportunity.get("canonical_id")).lower()),
            ("application_url", normalize_text(opportunity.get("application_url")).lower()),
            ("official_program_page", normalize_text(opportunity.get("official_program_page")).lower()),
            ("project_name", normalize_text(opportunity.get("project_name")).lower()),
        ]

    for opportunity in opportunities:
        variants = [(name, value) for name, value in key_variants(opportunity) if value]
        if not variants:
            deduped.append(opportunity)
            continue

        matched_indices = {
            key_to_index[f"{name}:{value}"]
            for name, value in variants
            if f"{name}:{value}" in key_to_index
        }

        if matched_indices:
            for idx in matched_indices:
                existing = deduped[idx]
                existing_score = confidence_score(existing)
                new_score = confidence_score(opportunity)
                if new_score > existing_score:
                    deduped[idx] = opportunity
            continue

        deduped.append(opportunity)
        new_index = len(deduped) - 1
        for name, value in variants:
            key_to_index[f"{name}:{value}"] = new_index

    return deduped


def process_rounds_and_generate_final():
    """Process all round outputs and generate final JSON with active SDG research funding opportunities."""
    all_active = []
    rounds_summary = {
        "total_rounds": TOTAL_ROUNDS,
        "successful_rounds": 0,
        "rounds_with_opportunities": 0,
        "rounds_with_issues": []
    }
    
    print(f"\n{'='*80}")
    print("📋 PROCESSING ROUND OUTPUTS")
    print(f"{'='*80}")
    
    for round_num in range(1, TOTAL_ROUNDS + 1):
        output_file = OUTPUT_DIR / f"round{round_num}_final.json"

        if not output_file.exists():
            print(f"⚠️  Round {round_num} output not found: {output_file}")
            round_summary = {"round": round_num, "status": "missing"}
            rounds_summary["rounds_with_issues"].append(round_summary)
            continue

        data = load_json_from_file(output_file)
        if data is None:
            print(f"❌ Round {round_num} JSON parse failed")
            round_summary = {"round": round_num, "status": "parse_failed"}
            rounds_summary["rounds_with_issues"].append(round_summary)
            continue
        
        # Handle different data formats - support both research and legacy startup schemas
        research_opportunities = None
        excluded_count = 0

        if "sdg_research_opportunities" in data:
            research_opportunities = data["sdg_research_opportunities"]
        elif "funding_opportunities" in data:
            research_opportunities = data["funding_opportunities"]
        elif "startup_funding_programs" in data:
            research_opportunities = data["startup_funding_programs"]
        elif "candidates" in data:
            research_opportunities = data["candidates"]
        elif isinstance(data, list):
            research_opportunities = data
        elif isinstance(data, dict) and data:
            research_opportunities = list(data.values())[0] if isinstance(list(data.values())[0], list) else [data]

        if isinstance(data, dict) and "excluded" in data:
            excluded_count = len(data["excluded"])

        if not research_opportunities:
            print(f"⚠️  Round {round_num}: no opportunities extracted")
            research_opportunities = []
        
        print(f"✅ Round {round_num}: {len(research_opportunities)} raw opportunities")
        
        # Normalize and validate
        normalized_opportunities = []
        for opp in research_opportunities:
            normalized = normalize_research_opportunity(opp)
            if normalized and is_valid_funding_opportunity(normalized):
                normalized_opportunities.append(normalized)
        
        print(f"   → {len(normalized_opportunities)} valid opportunities (status + deadline)")
        
        all_active.extend(normalized_opportunities)
        
        if len(normalized_opportunities) > 0:
            rounds_summary["rounds_with_opportunities"] += 1
        
        rounds_summary["successful_rounds"] += 1
    
    # Log summary
    print(f"\n{'='*80}")
    print(f"📊 PROCESSING SUMMARY")
    print(f"{'='*80}")
    print(f"[JSON] Successful rounds: {rounds_summary['successful_rounds']}/{rounds_summary['total_rounds']}")
    print(f"[JSON] Rounds with opportunities: {rounds_summary['rounds_with_opportunities']}")
    if rounds_summary["rounds_with_issues"]:
        print(f"[JSON] Issues in rounds: {rounds_summary['rounds_with_issues']}")
    
    unique_opportunities = dedupe_opportunities(all_active)
    
    final_data = {
        "total_count": len(unique_opportunities),
        "generated_date": datetime.now().strftime("%Y-%m-%d"),
        "generated_at": datetime.now().isoformat(),
        "funding_program_count": len(unique_opportunities),
        "sdg_research_opportunities": unique_opportunities,
        "processing_summary": rounds_summary
    }
    
    # Save to final file
    try:
        final_output_path = OUTPUT_DIR / "final_results.json"
        with open(final_output_path, "w", encoding="utf-8") as f:
            json.dump(final_data, f, indent=2, ensure_ascii=False)
        print(f"✅ Saved: {final_output_path}")
    except Exception as e:
        print(f"❌ Failed to save final results: {e}")

    # Export additional files
    try:
        mega_export_path = Path("mega_sdg_research_export.json")
        with open(mega_export_path, "w", encoding="utf-8") as f:
            json.dump(unique_opportunities, f, indent=2, ensure_ascii=False)
        print(f"✅ Saved: {mega_export_path}")
    except Exception as e:
        print(f"❌ Failed to save mega export: {e}")

    try:
        final_sdg_path = Path("final_sdg_research_funding.json")
        with open(final_sdg_path, "w", encoding="utf-8") as f:
            json.dump(unique_opportunities, f, indent=2, ensure_ascii=False)
        print(f"✅ Saved: {final_sdg_path}")
    except Exception as e:
        print(f"❌ Failed to save final SDG research: {e}")

    try:
        db_ready_path = Path("sdg_research_ready4db.json")
        archive_db_ready = Path("output") / "archive" / "sdg_research_ready4db.json"
        archive_db_ready.parent.mkdir(parents=True, exist_ok=True)
        
        export_data = {
            "metadata": {
                "generated_at": datetime.now().isoformat(),
                "total_opportunities": len(unique_opportunities),
                "schema_version": "1.0",
                "domain": "sdg_research_funding"
            },
            "opportunities": unique_opportunities
        }
        
        with open(db_ready_path, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=2, ensure_ascii=False)
        
        with open(archive_db_ready, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=2, ensure_ascii=False)
        
        print(f"✅ Saved: {db_ready_path}")
        print(f"✅ Archived: {archive_db_ready}")
    except Exception as e:
        print(f"❌ Failed to save DB-ready export: {e}")
    
    return final_data


# Load system prompt
try:
    with open(SYSTEM_PROMPT_FILE, "r", encoding="utf-8") as f:
        SYSTEM_PROMPT = f.read()
except FileNotFoundError:
    print(f"❌ System prompt file not found: {SYSTEM_PROMPT_FILE}")
    exit(1)

# Parse arguments
parser = argparse.ArgumentParser(description="Run OpenCode rounds for SDG research funding intelligence and consolidate output.")
parser.add_argument("--clear", action="store_true", help="Clear previous round outputs before running.")
parser.add_argument("--round-timeout", type=int, default=1200, help="Max seconds to wait per OpenCode round before timing out.")
args = parser.parse_args()

if args.clear:
    print("🗓️  Clearing previous outputs...")
    clear_previous_outputs()

print(f"\n{'='*80}")
print(f"🚀 SDG RESEARCH FUNDING INTELLIGENCE ENGINE")
print(f"Model: {MODEL} | Rounds: {TOTAL_ROUNDS}")
print(f"{'='*80}\n")

run_started_at = time.perf_counter()
previous_outputs = []

# Main scraper loop
for round_num in range(1, TOTAL_ROUNDS + 1):
    round_started_at = time.perf_counter()
    
    print(f"\n{'='*80}")
    print(f"🚀 ROUND {round_num}/{TOTAL_ROUNDS}")
    print(f"{'='*80}\n")
    
    # Build prompt based on round
    if round_num == 1:
        user_prompt = f"""
ROUND 1: MAJOR GOVERNMENT SDG RESEARCH ECOSYSTEMS

Discover major active SDG research funding ecosystems from:

GOVERNMENT AGENCIES:
- DST (Department of Science & Technology)
- SERB (Science and Engineering Research Board)
- ICMR (Indian Council of Medical Research)
- DBT (Department of Biotechnology)
- BIRAC (Biotechnology Industry Research Assistance Council)
- CSIR (Council of Scientific and Industrial Research)
- UGC (University Grants Commission)
- AICTE (All India Council for Technical Education)
- ISRO (Indian Space Research Organisation)
- DRDO (Defence Research and Development Organisation)
- MeitY (Ministry of Electronics and Information Technology)
- MNRE (Ministry of New and Renewable Energy)
- MoEFCC (Ministry of Environment, Forest and Climate Change)
- NITI Aayog

Search across all official portals, notification pages, and funding call databases.

Return ONLY active, operational SDG research funding opportunities.
Include grant details, eligibility, deadlines, ministries, research domains, SDG alignment.
"""
    elif round_num == 2:
        user_prompt = f"""
ROUND 2: CSR, PRIVATE, AND INTERNATIONAL SDG ECOSYSTEMS

Discover active SDG research funding from:

INTERNATIONAL ORGANIZATIONS:
- UNDP (United Nations Development Programme)
- UNICEF
- UNESCO
- World Bank
- Asian Development Bank (ADB)
- Gates Foundation
- Wellcome Trust
- Climate-KIC
- Grand Challenges
- GIZ (German International Cooperation)

CSR AND PRIVATE ECOSYSTEMS:
- Reliance Foundation
- Infosys Foundation
- Wipro Foundation
- Azim Premji Foundation
- Mahindra Foundation
- NASSCOM Foundation
- Social Alpha
- Villgro
- Acumen
- Omnivor Ventures

Include all active SDG research grants, fellowships, challenges, and accelerators.
"""
    else:
        user_prompt = f"""
ROUND 3: LONG-TAIL UNIVERSITY GRANTS AND EMERGING ECOSYSTEMS

Discover remaining SDG research funding opportunities:

INSTITUTIONAL RESEARCH PROGRAMS:
- IIT research grants and innovation programs
- NIT research ecosystems
- IIIT research grants
- University innovation missions
- State-level innovation programs
- Research consortiums
- District innovation systems

EMERGING ECOSYSTEMS:
- Ministry PDF notifications and announcements
- Hidden research grant databases
- Regional research initiatives
- Sectoral innovation programs
- Academia-industry collaboration grants
- Women-led research initiatives
- Climate and sustainability research programs

Search ministry websites, university portals, innovation portals for overlooked opportunities.
"""
    
    full_prompt = f"{SYSTEM_PROMPT}\n\n{user_prompt}"
    
    # Run OpenCode
    cmd = ["opencode", "run", "-m", MODEL, "--dangerously-skip-permissions"]
    
    print("⚡ Running OpenCode...\n")
    
    process = None
    master_fd = None
    output_chunks = []
    buffer = b""
    output_text = ""

    try:
        master_fd, slave_fd = pty.openpty()
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=slave_fd,
            stderr=slave_fd,
            text=False,
            close_fds=True,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        os.close(slave_fd)

        process.stdin.write(full_prompt.encode("utf-8"))
        process.stdin.close()

        start_wait = time.perf_counter()

        while True:
            ready, _, _ = select.select([master_fd], [], [], 0.25)
            if ready:
                try:
                    chunk = os.read(master_fd, 4096)
                except OSError:
                    chunk = b""

                if chunk:
                    buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        decoded = line.decode("utf-8", errors="replace").rstrip("\r")
                        print(decoded)
                        output_chunks.append(decoded)
                elif process.poll() is not None:
                    break

            if process.poll() is not None and not ready:
                break

            if time.perf_counter() - start_wait > args.round_timeout:
                raise subprocess.TimeoutExpired(cmd, args.round_timeout)

        if buffer:
            decoded = buffer.decode("utf-8", errors="replace").rstrip("\r\n")
            if decoded:
                print(decoded)
                output_chunks.append(decoded)

        process.wait(timeout=5)
        output_text = "\n".join(output_chunks)

        if process.returncode != 0:
            print(f"\n❌ OpenCode exited with code {process.returncode}")
            
    except subprocess.TimeoutExpired:
        print(f"\n❌ OpenCode timed out after {args.round_timeout} seconds")

        if buffer:
            decoded = buffer.decode("utf-8", errors="replace").rstrip("\r\n")
            if decoded:
                output_chunks.append(decoded)
        output_text = "\n".join(output_chunks)

        try:
            if process is not None:
                process.kill()
                process.wait()
        except Exception:
            pass
    except Exception as e:
        print(f"\n❌ Error running OpenCode: {e}")
        if buffer:
            decoded = buffer.decode("utf-8", errors="replace").rstrip("\r\n")
            if decoded:
                output_chunks.append(decoded)
        output_text = "\n".join(output_chunks)
    finally:
        if master_fd is not None:
            try:
                os.close(master_fd)
            except OSError:
                pass
    
    # Save output
    raw_output_file = OUTPUT_DIR / f"round{round_num}_raw.txt"
    json_output_file = OUTPUT_DIR / f"round{round_num}_raw.json"
    final_snapshot_file = OUTPUT_DIR / f"round{round_num}_final.json"

    try:
        # Save raw terminal output for debugging
        with open(raw_output_file, "w", encoding="utf-8") as f:
            f.write(output_text if output_text else "")

        if output_text:
            print(f"\n✅ Saved raw output: {raw_output_file} ({len(output_text)} bytes)")

        extracted_json, source_path = resolve_round_json(round_num, output_text)

        if extracted_json:
            # Save extracted JSON
            with open(json_output_file, "w", encoding="utf-8") as f:
                json.dump(extracted_json, f, indent=2, ensure_ascii=False)

            with open(final_snapshot_file, "w", encoding="utf-8") as f:
                json.dump(extracted_json, f, indent=2, ensure_ascii=False)

            # Track most recent resolved JSON for debugging
            with open(OUTPUT_DIR / "final_results.json", "w", encoding="utf-8") as f:
                json.dump(extracted_json, f, indent=2, ensure_ascii=False)

            candidate_count = 0
            excluded_count = 0
            if isinstance(extracted_json, dict):
                if "sdg_research_opportunities" in extracted_json:
                    candidate_count = len(extracted_json["sdg_research_opportunities"])
                elif "funding_opportunities" in extracted_json:
                    candidate_count = len(extracted_json["funding_opportunities"])
                elif "startup_funding_programs" in extracted_json:
                    candidate_count = len(extracted_json["startup_funding_programs"])
                elif "candidates" in extracted_json:
                    candidate_count = len(extracted_json["candidates"])
                if "excluded" in extracted_json:
                    excluded_count = len(extracted_json.get("excluded") or [])
            elif isinstance(extracted_json, list):
                candidate_count = len(extracted_json)

            print(f"✅ Extracted JSON: {json_output_file} ({candidate_count} opportunities)")
            if excluded_count > 0:
                print(f"[JSON] Excluded count: {excluded_count}")
            print(f"[JSON] Opportunities found: {candidate_count}")
            previous_outputs.append(output_text)
        else:
            # Fallback: save empty structure
            with open(json_output_file, "w", encoding="utf-8") as f:
                json.dump({"sdg_research_opportunities": []}, f, indent=2)

            with open(final_snapshot_file, "w", encoding="utf-8") as f:
                json.dump({"sdg_research_opportunities": []}, f, indent=2)

            print(f"⚠️  No JSON extracted from round {round_num} output")
            print(f"[JSON] Parse failed - saved empty opportunities")
            previous_outputs.append("{}")

    except Exception as e:
        print(f"\n❌ Error saving round {round_num}: {e}")
        try:
            with open(json_output_file, "w", encoding="utf-8") as f:
                json.dump({"sdg_research_opportunities": []}, f, indent=2)
            with open(final_snapshot_file, "w", encoding="utf-8") as f:
                json.dump({"sdg_research_opportunities": []}, f, indent=2)
        except Exception:
            pass
        previous_outputs.append("{}")
    
    print(f"⏱️  Round {round_num} took {time.perf_counter() - round_started_at:.2f}s")

print(f"\n{'='*80}")
print("🎯 ALL ROUNDS COMPLETED")
print(f"{'='*80}")

# Process outputs
final_data = process_rounds_and_generate_final()

total_elapsed = time.perf_counter() - run_started_at
print(f"\n⏱️  Total runtime: {total_elapsed:.2f}s")
print(f"\n✨ Intelligence Engine Complete! Generated {final_data['funding_program_count']} active SDG research funding opportunities\n")
