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

MODEL = "bigpickle"
TOTAL_ROUNDS = 3

BASE_DIR = Path(__file__).resolve().parent
SYSTEM_PROMPT_FILE = BASE_DIR / "systemprompt.md"


MODEL_ALIASES = {
    "minimaxm2.5": "opencode/minimax-m2.5-free",
    "gpt5mini": "github-copilot/gpt-5-mini",
    "kimi2.6": "cloudflare-workers-ai/@cf/moonshotai/kimi-k2.6",
    "ring2.6": "opencode/ring-2.6-1t-free",
    "nemotron3": "opencode/nemotron-3-super-free",
    "bigpickle": "opencode/big-pickle",
    "gemini3pro": "google/gemini-3-pro-preview",
    "minimax-m2.5-free": "opencode/minimax-m2.5-free",
    "gpt-5-mini": "github-copilot/gpt-5-mini",
    "kimi-k2.6": "cloudflare-workers-ai/@cf/moonshotai/kimi-k2.6",
    "ring-2.6-1t-free": "opencode/ring-2.6-1t-free",
    "nemotron-3-super-free": "opencode/nemotron-3-super-free",
    "big-pickle": "opencode/big-pickle",
    "gemini-3-pro-preview": "google/gemini-3-pro-preview",
}


def resolve_model_alias(model_name):
    value = (model_name or "").strip()
    if not value:
        return MODEL_ALIASES[MODEL]
    if value in MODEL_ALIASES:
        return MODEL_ALIASES[value]
    if "/" in value:
        return value
    return MODEL_ALIASES.get(value, value)



ACTIVE_STATUSES = {
    "application_open",
    "active",
    "rolling_applications",
    "cohort_open",
    "incubation_open",
    "accepting_startups",
    "recurring",
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
    "recurring",
    "always_open",
    "cohort_open",
    "incubation_open",
}

OUTPUT_DIR = BASE_DIR / "round_outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

ARCHIVE_DIR = BASE_DIR / "output" / "archive"


def clear_previous_outputs():
    """
    Remove previous round outputs and archive old final results.
    
    - Archive old final_result.json with timestamp before clearing
    - Clear all JSON files in V2-Startup folder and round_outputs/
    - Clear JSON files in outputs/archive/ if present
    - Store old results in output/archive/ for backup/debugging
    """
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    
    # Step 1: Archive old final_result.json files before clearing
    files_to_archive = [
        BASE_DIR / "final_results.json",
        BASE_DIR / "mega_funding_export.json",
        BASE_DIR / "final_startup_funding.json",
        BASE_DIR / "startup_funding_ready4db.json",
    ]
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    for file_path in files_to_archive:
        if file_path.exists():
            try:
                archived_name = f"{file_path.stem}_{timestamp}.json"
                archived_path = ARCHIVE_DIR / archived_name
                file_path.rename(archived_path)
                print(f"📦 Archived: {file_path.name} → {archived_path.relative_to(BASE_DIR)}")
            except Exception as e:
                print(f"⚠️  Could not archive {file_path.name}: {e}")
    
    # Step 2: Clear all JSON/TXT files in round_outputs/
    print(f"\n🗑️  Clearing round_outputs/...")
    for file_path in OUTPUT_DIR.glob("round*_raw.json"):
        file_path.unlink(missing_ok=True)
    for file_path in OUTPUT_DIR.glob("round*_raw.txt"):
        file_path.unlink(missing_ok=True)
    for file_path in OUTPUT_DIR.glob("round*_final.json"):
        file_path.unlink(missing_ok=True)
    
    (OUTPUT_DIR / "final_results.json").unlink(missing_ok=True)
    
    # Step 3: Clear any JSON files in outputs/archive/ if it exists
    outputs_archive = BASE_DIR / "outputs" / "archive"
    if outputs_archive.exists():
        print(f"🗑️  Clearing outputs/archive/...")
        for file_path in outputs_archive.glob("*.json"):
            try:
                file_path.unlink(missing_ok=True)
                print(f"  Removed: {file_path.name}")
            except Exception as e:
                print(f"  ⚠️  Could not remove {file_path.name}: {e}")
    
    # Step 4: Verify V2-Startup folder is clean of JSON files
    print(f"🗑️  Verifying V2-Startup folder cleanup...")
    v2_startup_jsons = list(BASE_DIR.glob("*.json"))
    if not v2_startup_jsons:
        print(f"  ✅ V2-Startup folder is clean (no JSON files)\n")
    else:
        print(f"  ℹ️  Remaining JSON files in V2-Startup: {len(v2_startup_jsons)}\n")


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
                data = json.loads(block)
                print(f"[JSON] Extracted from markdown fence: {type(data).__name__} with {len(data) if isinstance(data, (list, dict)) else 'N/A'} items")
                # Normalize wrapped arrays
                if isinstance(data, list) and data and isinstance(data[0], dict):
                    if any(key in data[0] for key in ["program_name", "application_url", "program_type"]):
                        return {"funding_opportunities": data}
                return data
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
            bracket_count = 0
            for i in range(start_idx, len(text)):
                if text[i] == '[':
                    bracket_count += 1
                elif text[i] == ']':
                    bracket_count -= 1
                    if bracket_count == 0:
                        try:
                            json_str = text[start_idx:i+1]
                            data = json.loads(json_str)
                            print(f"[JSON] Extracted array: {len(data)} items")
                            # Wrap raw arrays of candidates
                            if isinstance(data, list) and data and isinstance(data[0], dict):
                                if any(key in data[0] for key in ["program_name", "program_type", "application_url"]):
                                    return {"funding_opportunities": data}
                            return data
                        except json.JSONDecodeError:
                            pass
        else:
            brace_count = 0
            for i in range(start_idx, len(text)):
                if text[i] == '{':
                    brace_count += 1
                elif text[i] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        try:
                            json_str = text[start_idx:i+1]
                            data = json.loads(json_str)
                            print(f"[JSON] Extracted object with keys: {list(data.keys())[:3] if isinstance(data, dict) else 'N/A'}")
                            return data
                        except json.JSONDecodeError:
                            pass
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
        OUTPUT_DIR / "final_results.json",
        Path("final_results.json"),
        OUTPUT_DIR / f"round{round_num}_final.json",
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


def build_previous_round_summary(extracted_json, round_num):
    """Build a compact summary for the next round prompt."""
    if isinstance(extracted_json, dict):
        programs = (
            extracted_json.get("funding_opportunities")
            or extracted_json.get("startup_funding_programs")
            or extracted_json.get("funding_programs")
            or extracted_json.get("candidates")
            or []
        )
    elif isinstance(extracted_json, list):
        programs = extracted_json
    else:
        programs = []

    compact_programs = []
    for program in programs[:25]:
        if not isinstance(program, dict):
            continue
        compact_programs.append(
            {
                "program_name": normalize_text(program.get("program_name") or program.get("program")),
                "program_type": normalize_text(program.get("program_type")),
                "organization": normalize_text(program.get("organization") or program.get("program_owner")),
                "canonical_id": normalize_text(program.get("canonical_id")),
                "source_url": normalize_text(program.get("source_url")),
                "label": normalize_text(program.get("label") or "startup") or "startup",
            }
        )

    return json.dumps(
        {
            "round": round_num,
            "count": len(programs),
            "programs": compact_programs,
        },
        indent=2,
        ensure_ascii=False,
    )


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


def coerce_sdg_value(value):
    """Return a numeric SDG id from values like 9, "9", "SDG9", or "SDG 9"."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        sdg = int(value)
        return sdg if 1 <= sdg <= 17 else None

    match = re.search(r"(?:sdg\s*)?(\d{1,2})", normalize_text(value).lower())
    if not match:
        return None

    sdg = int(match.group(1))
    return sdg if 1 <= sdg <= 17 else None


def normalize_sdg_alignment(values):
    if values is None:
        return []
    if not isinstance(values, (list, tuple, set)):
        values = [values]

    normalized = set()
    for value in values:
        if isinstance(value, str) and "," in value:
            candidates = value.split(",")
        else:
            candidates = [value]

        for candidate in candidates:
            sdg = coerce_sdg_value(candidate)
            if sdg is not None:
                normalized.add(sdg)

    return sorted(normalized)


def infer_sdg_alignment(program):
    text_fields = [
        normalize_text(program.get("sector")),
        normalize_text(program.get("program_type")),
        " ".join([normalize_text(x) for x in (program.get("focus_areas") or [])]),
        " ".join([normalize_text(x) for x in (program.get("tags") or [])]),
        " ".join([normalize_text(x) for x in (program.get("benefits") or [])]),
    ]
    blob = " ".join(text_fields).lower()

    keyword_to_sdg = {
        "health": 3,
        "education": 4,
        "climate": 13,
        "agri": 2,
        "agriculture": 2,
        "women": 5,
    }

    inferred = set(normalize_sdg_alignment(program.get("sdg_alignment")))
    for token, sdg in keyword_to_sdg.items():
        if token in blob:
            inferred.add(sdg)
    return sorted(inferred)


def build_canonical_id(program):
    existing = normalize_text(program.get("canonical_id"))
    if existing:
        return existing

    parts = [
        normalize_text(program.get("program_name")).lower(),
        normalize_text(program.get("application_url")).lower(),
        normalize_text(program.get("official_program_page")).lower(),
        normalize_text(program.get("official_website")).lower(),
        normalize_text(program.get("source_url")).lower(),
    ]
    source = "|".join([p for p in parts if p])
    if not source:
        return ""
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]


def confidence_score(program):
    verification = program.get("verification") or {}
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


def normalize_program(raw_program):
    if not isinstance(raw_program, dict):
        return None

    funding_support = raw_program.get("funding_support") if isinstance(raw_program.get("funding_support"), dict) else {}
    eligibility = raw_program.get("eligibility") if isinstance(raw_program.get("eligibility"), dict) else {}
    geography = raw_program.get("geography") if isinstance(raw_program.get("geography"), dict) else {}
    verification = raw_program.get("verification") if isinstance(raw_program.get("verification"), dict) else {}
    prizes = raw_program.get("prizes") if isinstance(raw_program.get("prizes"), dict) else {}
    eligibility_criteria = raw_program.get("eligibility_criteria") if isinstance(raw_program.get("eligibility_criteria"), dict) else {}

    normalized = {
        "label": normalize_text(raw_program.get("label") or "startup") or "startup",
        "program_name": normalize_text(raw_program.get("program_name") or raw_program.get("hackathon_name") or raw_program.get("full_name")),
        "program_type": normalize_text(raw_program.get("program_type") or raw_program.get("event_type")),
        "status": normalize_text(raw_program.get("status") or raw_program.get("current_status") or "active").lower(),
        "deadline_type": normalize_text(raw_program.get("deadline_type")),
        "deadline": normalize_text(raw_program.get("deadline") or raw_program.get("application_deadline") or raw_program.get("registration_close_date")),
        "deadline_iso": normalize_text(raw_program.get("deadline_iso") or raw_program.get("deadline") or raw_program.get("application_deadline")),
        "application_url": normalize_text(raw_program.get("application_url") or raw_program.get("registration_url") or raw_program.get("submission_url")),
        "official_website": normalize_text(raw_program.get("official_website")),
        "official_program_page": normalize_text(raw_program.get("official_program_page") or raw_program.get("official_event_page")),
        "source_url": normalize_text(raw_program.get("source_url")),
        "organization": normalize_text(raw_program.get("organization") or raw_program.get("hosting_organization") or raw_program.get("ministry")),
        "program_owner": normalize_text(raw_program.get("program_owner") or raw_program.get("ministry") or raw_program.get("hosting_organization")),
        "sector": normalize_text(raw_program.get("sector") or raw_program.get("domain") or raw_program.get("theme")),
        "focus_areas": list(raw_program.get("focus_areas") or []),
        "benefits": list(raw_program.get("benefits") or []),
        "funding_support": {
            "type": normalize_text(funding_support.get("type") or prizes.get("summary")),
            "amount_min": funding_support.get("amount_min"),
            "amount_max": funding_support.get("amount_max"),
            "currency": normalize_text(funding_support.get("currency") or "INR") or "INR",
            "equity_taken": normalize_text(funding_support.get("equity_taken")),
            "grant_or_equity": normalize_text(funding_support.get("grant_or_equity")),
        },
        "eligibility": {
            "summary": normalize_text(eligibility.get("summary") or eligibility_criteria.get("summary"))
        },
        "startup_stage": list(raw_program.get("startup_stage") or []),
        "incubation_support": list(raw_program.get("incubation_support") or []),
        "mentorship_support": list(raw_program.get("mentorship_support") or []),
        "geography": {
            "country": normalize_text(geography.get("country") or "India") or "India",
            "state": normalize_text(geography.get("state")),
            "city": normalize_text(geography.get("city")),
        },
        "sdg_alignment": normalize_sdg_alignment(raw_program.get("sdg_alignment")),
        "is_government": coerce_bool(raw_program.get("is_government")),
        "is_private": coerce_bool(raw_program.get("is_private")),
        "is_academic": coerce_bool(raw_program.get("is_academic")),
        "is_corporate": coerce_bool(raw_program.get("is_corporate")),
        "verification": {
            "official_source": coerce_bool(verification.get("official_source", True)),
            "official_url_confirmed": coerce_bool(verification.get("official_url_confirmed", True)),
            "deadline_verified": coerce_bool(verification.get("deadline_verified", True)),
            "active_confirmation": coerce_bool(verification.get("active_confirmation", True)),
            "aggregator_only": coerce_bool(verification.get("aggregator_only", False)),
            "confidence": normalize_text(verification.get("confidence") or "high") or "high",
        },
        "canonical_id": normalize_text(raw_program.get("canonical_id")),
        "tags": list(raw_program.get("tags") or []),
    }

    normalized["sdg_alignment"] = infer_sdg_alignment(normalized)
    normalized["canonical_id"] = build_canonical_id(normalized)
    return normalized


def is_program_active(program):
    status = normalize_text(program.get("status")).lower()
    if status in REJECTED_STATUSES:
        return False
    if status in ACTIVE_STATUSES:
        return True
    return status == ""


def is_deadline_valid(program):
    status = normalize_text(program.get("status")).lower()
    deadline_type = normalize_text(program.get("deadline_type")).lower()
    deadline_value = normalize_text(program.get("deadline_iso") or program.get("deadline"))

    if deadline_type in ROLLING_DEADLINE_TYPES:
        return True
    if status in {"rolling_applications", "recurring", "cohort_open", "incubation_open", "accepting_startups"}:
        return True
    if status in REJECTED_STATUSES:
        return False
    if not deadline_value:
        return status in ACTIVE_STATUSES

    try:
        if "T" in deadline_value:
            deadline = datetime.fromisoformat(deadline_value.replace("Z", "+00:00")).date()
        else:
            deadline = datetime.strptime(deadline_value[:10], "%Y-%m-%d").date()
        return deadline >= date.today()
    except (ValueError, TypeError):
        return status in ACTIVE_STATUSES


def is_valid_funding_opportunity(program):
    return is_program_active(program) and is_deadline_valid(program)


def dedupe_opportunities(opportunities):
    deduped = []
    key_to_index = {}

    def key_variants(program):
        return [
            ("canonical_id", normalize_text(program.get("canonical_id")).lower()),
            ("application_url", normalize_text(program.get("application_url")).lower()),
            ("official_website", normalize_text(program.get("official_website")).lower()),
            ("program_name", normalize_text(program.get("program_name")).lower()),
        ]

    for program in opportunities:
        variants = [(name, value) for name, value in key_variants(program) if value]
        if not variants:
            continue

        matched_indices = {
            key_to_index[f"{name}:{value}"]
            for name, value in variants
            if f"{name}:{value}" in key_to_index
        }

        if matched_indices:
            best_index = min(matched_indices)
            if confidence_score(program) > confidence_score(deduped[best_index]):
                deduped[best_index] = program
            for name, value in variants:
                key_to_index[f"{name}:{value}"] = best_index
            continue

        deduped.append(program)
        new_index = len(deduped) - 1
        for name, value in variants:
            key_to_index[f"{name}:{value}"] = new_index

    return deduped


def process_rounds_and_generate_final():
    """Process all round outputs and generate final JSON with active startup funding opportunities."""
    all_active = []
    rounds_summary = {
        "total_rounds": TOTAL_ROUNDS,
        "successful_rounds": 0,
        "rounds_with_funding_opportunities": 0,
        "rounds_with_issues": []
    }
    
    print(f"\n{'='*80}")
    print("📋 PROCESSING ROUND OUTPUTS")
    print(f"{'='*80}")
    
    for round_num in range(1, TOTAL_ROUNDS + 1):
        output_file = OUTPUT_DIR / f"round{round_num}_final.json"

        if not output_file.exists():
            msg = f"Round {round_num}: No final JSON snapshot found"
            print(f"\n⚠️  {msg}")
            rounds_summary["rounds_with_issues"].append(msg)
            continue

        data = load_json_from_file(output_file)
        if data is None:
            msg = f"Round {round_num}: Error parsing JSON snapshot"
            print(f"\n⚠️  {msg}")
            rounds_summary["rounds_with_issues"].append(msg)
            continue
        
        # Handle different data formats
        funding_opportunities = None
        excluded_count = 0

        if "funding_opportunities" in data:
            funding_opportunities = data.get("funding_opportunities", [])
        elif "startup_funding_programs" in data:
            funding_opportunities = data.get("startup_funding_programs", [])
        elif "candidates" in data:
            funding_opportunities = data.get("candidates", [])
        elif isinstance(data, list):
            funding_opportunities = data
        elif isinstance(data, dict) and data:
            funding_opportunities = list(data.values()) if all(isinstance(v, dict) for v in data.values()) else None

        if isinstance(data, dict) and "excluded" in data:
            excluded_count = len(data.get("excluded") or [])

        if isinstance(funding_opportunities, list):
            print(f"\n[JSON] Round {round_num}: Found {len(funding_opportunities)} opportunities")

            normalized = [normalize_program(p) for p in funding_opportunities]
            normalized = [p for p in normalized if p is not None]
            shortlisted = [p for p in normalized if is_valid_funding_opportunity(p)]
            excluded_count = max(excluded_count, len(normalized) - len(shortlisted))

            all_active.extend(shortlisted)
            rounds_summary["successful_rounds"] += 1
            if len(funding_opportunities) > 0 or excluded_count > 0:
                rounds_summary["rounds_with_funding_opportunities"] += 1

            print(f"[JSON] Round {round_num}: {len(normalized)} normalized → {len(shortlisted)} valid active opportunities")
            print(f"[JSON] Excluded: {excluded_count} (inactive/closed/expired)")
        elif funding_opportunities is None:
            msg = f"Round {round_num}: Could not parse funding opportunities from JSON"
            print(f"\n⚠️  {msg}")
            rounds_summary["rounds_with_issues"].append(msg)
        else:
            msg = f"Round {round_num}: Opportunities not in expected array format"
            print(f"\n⚠️  {msg}")
            rounds_summary["rounds_with_issues"].append(msg)
    
    # Log summary
    print(f"\n{'='*80}")
    print(f"📊 PROCESSING SUMMARY")
    print(f"{'='*80}")
    print(f"[JSON] Successful rounds: {rounds_summary['successful_rounds']}/{rounds_summary['total_rounds']}")
    print(f"[JSON] Rounds with opportunities: {rounds_summary['rounds_with_funding_opportunities']}")
    if rounds_summary["rounds_with_issues"]:
        print(f"[JSON] Issues encountered:")
        for issue in rounds_summary["rounds_with_issues"]:
            print(f"  - {issue}")
    
    unique_opportunities = dedupe_opportunities(all_active)
    
    final_data = {
        "total_count": len(unique_opportunities),
        "generated_date": datetime.now().strftime("%Y-%m-%d"),
        "generated_at": datetime.now().isoformat(),
        "funding_program_count": len(unique_opportunities),
        "funding_opportunities": unique_opportunities,
        "startup_funding_programs": unique_opportunities,
        "processing_summary": rounds_summary
    }
    
    # Save to final file
    try:
        final_file = BASE_DIR / "final_results.json"
        with open(final_file, "w", encoding="utf-8") as f:
            json.dump(final_data, f, indent=2, ensure_ascii=False)
        
        print(f"\n✅ Generated {len(unique_opportunities)} unique startup funding opportunities")
        print(f"📁 Saved to: {final_file}")
        print(f"[JSON] Parse success - all files processed")
    except Exception as e:
        print(f"\n❌ Error saving final results: {e}")

    # Export additional files
    try:
        mega_file = BASE_DIR / "mega_funding_export.json"
        with open(mega_file, "w", encoding="utf-8") as f:
            json.dump({"funding_opportunities": unique_opportunities}, f, indent=2, ensure_ascii=False)
        print(f"📁 Saved to: {mega_file}")
    except Exception as e:
        print(f"\n❌ Error saving mega_funding_export.json: {e}")

    try:
        final_startup_file = BASE_DIR / "final_startup_funding.json"
        with open(final_startup_file, "w", encoding="utf-8") as f:
            json.dump({"funding_opportunities": unique_opportunities}, f, indent=2, ensure_ascii=False)
        print(f"📁 Saved to: {final_startup_file}")
    except Exception as e:
        print(f"\n❌ Error saving final_startup_funding.json: {e}")

    try:
        startup_ready_file = BASE_DIR / "startup_funding_ready4db.json"
        with open(startup_ready_file, "w", encoding="utf-8") as f:
            json.dump({"funding_opportunities": unique_opportunities}, f, indent=2, ensure_ascii=False)
        print(f"📁 Saved to: {startup_ready_file}")

        archive_dir = BASE_DIR / "output" / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_ready_file = archive_dir / "startup_funding_ready4db.json"
        with open(archive_ready_file, "w", encoding="utf-8") as f:
            json.dump({"funding_opportunities": unique_opportunities}, f, indent=2, ensure_ascii=False)
        print(f"📁 Saved to: {archive_ready_file}")
    except Exception as e:
        print(f"\n❌ Error saving startup_funding_ready4db.json: {e}")
    
    return final_data


# Load system prompt
try:
    with open(SYSTEM_PROMPT_FILE, "r", encoding="utf-8") as f:
        SYSTEM_PROMPT = f.read()
except FileNotFoundError:
    print(f"❌ System prompt file not found: {SYSTEM_PROMPT_FILE}")
    exit(1)

# Parse arguments
parser = argparse.ArgumentParser(description="Run OpenCode rounds and consolidate output.")
parser.add_argument("--clear", action="store_true", help="Clear previous round outputs before running.")
parser.add_argument("--model", "--MODEL", dest="model", default=MODEL, help="Model alias to use for generation")
args = parser.parse_args()

REQUESTED_MODEL = args.model or MODEL
MODEL = resolve_model_alias(REQUESTED_MODEL)

if args.clear:
    print("🗑️  Clearing previous outputs...")
    clear_previous_outputs()

print(f"\n{'='*80}")
print(f"🚀 STARTUP FUNDING INTELLIGENCE ENGINE")
if REQUESTED_MODEL != MODEL:
    print(f"Model: {REQUESTED_MODEL} -> {MODEL} | Rounds: {TOTAL_ROUNDS}")
else:
    print(f"Model: {MODEL} | Rounds: {TOTAL_ROUNDS}")
print(f"{'='*80}\n")

run_started_at = time.perf_counter()
previous_round_summaries = []

# Main scraper loop
for round_num in range(1, TOTAL_ROUNDS + 1):
    round_started_at = time.perf_counter()
    
    print(f"\n{'='*80}")
    print(f"🚀 ROUND {round_num}/{TOTAL_ROUNDS}")
    print(f"{'='*80}\n")
    
    # Build prompt
    if round_num == 1:
        user_prompt = """
Round 1 objective:
Discover major national startup ecosystems in India focused on startup grants,
private startup funding, incubation programs, accelerator cohorts, university incubators,
seed support, innovation missions, and SDG/deeptech startup support systems.

Mandatory discovery domains include:
- startupindia.gov.in
- meity.gov.in
- msh.meity.gov.in
- aim.gov.in
- atalinnovationmission.gov.in
- birac.nic.in
- dst.gov.in
- stpi.in
- t-hub.co
- startupmission.kerala.gov.in
- IIT, IIIT, NIT, and university incubators
- CSR-backed startup accelerator ecosystems

Return STRICT JSON ONLY.
"""
    elif round_num == 2:
        previous_json = "\n\n".join(previous_round_summaries)
        user_prompt = f"""
Previously discovered opportunities:

{previous_json}

Round 2 objective:
Discover sectoral, state, and corporate incubators/funding programs NOT already found.

DO NOT repeat previously discovered opportunities.
Return STRICT JSON ONLY.
"""
    else:
        previous_json = "\n\n".join(previous_round_summaries)
        user_prompt = f"""
Previously discovered opportunities:

{previous_json}

Round 3 objective:
Discover long-tail university incubators, local startup missions,
PDF announcements, and emerging startup ecosystems.

DO NOT repeat previously discovered opportunities.
Return STRICT JSON ONLY.
"""
    
    full_prompt = f"{SYSTEM_PROMPT}\n\n{user_prompt}"
    
    # Run OpenCode
    cmd = ["opencode", "run", "-m", MODEL, "--dangerously-skip-permissions"]
    
    print("⚡ Running OpenCode...\n")
    
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

        output_chunks = []
        process.stdin.write(full_prompt.encode("utf-8"))
        process.stdin.close()

        start_wait = time.perf_counter()
        buffer = b""

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

            if time.perf_counter() - start_wait > 600:
                raise subprocess.TimeoutExpired(cmd, 600)

        if buffer:
            decoded = buffer.decode("utf-8", errors="replace").rstrip("\r\n")
            if decoded:
                print(decoded)
                output_chunks.append(decoded)

        process.wait(timeout=5)
        os.close(master_fd)
        output_text = "\n".join(output_chunks)

        if process.returncode != 0:
            print(f"\n❌ OpenCode exited with code {process.returncode}")
            
    except subprocess.TimeoutExpired:
        print(f"\n❌ OpenCode timed out after 600 seconds")
        try:
            process.kill()
            process.wait()
        except Exception:
            pass
        output_text = ""
    except Exception as e:
        print(f"\n❌ Error running OpenCode: {e}")
        output_text = ""
    
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
                if "funding_opportunities" in extracted_json:
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
            previous_round_summaries.append(build_previous_round_summary(extracted_json, round_num))
        else:
            # Fallback: save empty structure
            with open(json_output_file, "w", encoding="utf-8") as f:
                json.dump({"funding_opportunities": []}, f, indent=2)

            with open(final_snapshot_file, "w", encoding="utf-8") as f:
                json.dump({"funding_opportunities": []}, f, indent=2)

            print(f"⚠️  No JSON extracted from round {round_num} output")
            print(f"[JSON] Parse failed - saved empty opportunities")
            previous_round_summaries.append(json.dumps({"round": round_num, "count": 0, "programs": []}, indent=2, ensure_ascii=False))

    except Exception as e:
        print(f"\n❌ Error saving round {round_num}: {e}")
        try:
            with open(json_output_file, "w", encoding="utf-8") as f:
                json.dump({"funding_opportunities": []}, f, indent=2)
            with open(final_snapshot_file, "w", encoding="utf-8") as f:
                json.dump({"funding_opportunities": []}, f, indent=2)
        except Exception:
            pass
        previous_round_summaries.append(json.dumps({"round": round_num, "count": 0, "programs": []}, indent=2, ensure_ascii=False))
    
    print(f"⏱️  Round {round_num} took {time.perf_counter() - round_started_at:.2f}s")

print(f"\n{'='*80}")
print("🎯 ALL ROUNDS COMPLETED")
print(f"{'='*80}")

# Process outputs
final_data = process_rounds_and_generate_final()

total_elapsed = time.perf_counter() - run_started_at
print(f"\n⏱️  Total runtime: {total_elapsed:.2f}s")
print(f"\n✨ Scraper Complete! Generated {final_data['funding_program_count']} active startup funding opportunities\n")
