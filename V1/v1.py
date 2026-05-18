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
SYSTEM_PROMPT_FILE = "systemprompt.md"
TOTAL_ROUNDS = 3


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

    final_file = Path("final_results.json")
    final_file.unlink(missing_ok=True)


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
                    if "hackathon_name" in data[0] or "event_type" in data[0]:
                        return {"candidates": data}
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
                                if any(key in data[0] for key in ["hackathon_name", "event_type", "registration_url"]):
                                    return {"candidates": data}
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


def extract_json_from_text(text):
    """Legacy wrapper for backward compatibility."""
    return extract_json_payload(text)


def filter_by_deadline(candidates):
    """Filter candidates with deadline greater than today."""
    today = date.today()
    filtered = []
    
    for candidate in candidates:
        deadline_str = candidate.get("deadline")
        if not deadline_str:
            continue
        
        try:
            if "T" in str(deadline_str):
                deadline = datetime.fromisoformat(str(deadline_str).replace("Z", "+00:00")).date()
            else:
                deadline = datetime.strptime(str(deadline_str)[:10], "%Y-%m-%d").date()
            
            if deadline > today:
                filtered.append(candidate)
        except (ValueError, TypeError):
            pass
    
    return filtered


def process_rounds_and_generate_final():
    """Process all round outputs and generate final JSON with active hackathons."""
    all_active = []
    rounds_summary = {
        "total_rounds": TOTAL_ROUNDS,
        "successful_rounds": 0,
        "rounds_with_candidates": 0,
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
        candidates = None
        excluded_count = 0

        if "candidates" in data:
            candidates = data.get("candidates", [])
        elif "government_hackathons" in data:
            candidates = data.get("government_hackathons", [])
        elif isinstance(data, list):
            candidates = data
        elif isinstance(data, dict) and data:
            candidates = list(data.values()) if all(isinstance(v, dict) for v in data.values()) else None

        if isinstance(data, dict) and "excluded" in data:
            excluded_count = len(data.get("excluded") or [])

        if isinstance(candidates, list):
            print(f"\n[JSON] Round {round_num}: Found {len(candidates)} candidates")

            # Filter for active status and future deadlines
            active_candidates = [
                c for c in candidates
                if c.get("current_status") == "active" or c.get("current_status") is None
            ]

            # Filter by deadline
            shortlisted = filter_by_deadline(active_candidates)
            excluded_count = max(excluded_count, len(candidates) - len(shortlisted))

            all_active.extend(shortlisted)
            rounds_summary["successful_rounds"] += 1
            if len(candidates) > 0 or excluded_count > 0:
                rounds_summary["rounds_with_candidates"] += 1

            print(f"[JSON] Round {round_num}: {len(candidates)} total → "
                  f"{len(active_candidates)} active → "
                  f"{len(shortlisted)} with future deadlines")
            print(f"[JSON] Excluded: {excluded_count} (inactive/closed/past deadline)")
        elif candidates is None:
            msg = f"Round {round_num}: Could not parse candidates from JSON"
            print(f"\n⚠️  {msg}")
            rounds_summary["rounds_with_issues"].append(msg)
        else:
            msg = f"Round {round_num}: Candidates not in expected array format"
            print(f"\n⚠️  {msg}")
            rounds_summary["rounds_with_issues"].append(msg)
    
    # Log summary
    print(f"\n{'='*80}")
    print(f"📊 PROCESSING SUMMARY")
    print(f"{'='*80}")
    print(f"[JSON] Successful rounds: {rounds_summary['successful_rounds']}/{rounds_summary['total_rounds']}")
    print(f"[JSON] Rounds with candidates: {rounds_summary['rounds_with_candidates']}")
    if rounds_summary["rounds_with_issues"]:
        print(f"[JSON] Issues encountered:")
        for issue in rounds_summary["rounds_with_issues"]:
            print(f"  - {issue}")
    
    # Remove duplicates by multiple fields
    unique_hackathons = {}
    for hack in all_active:
        keys = [
            (hack.get("hackathon_name") or "").strip().lower(),
            (hack.get("source_url") or "").strip().lower(),
            (hack.get("registration_url") or "").strip().lower(),
        ]
        dedupe_key = "|".join([k for k in keys if k])
        if dedupe_key and dedupe_key not in unique_hackathons:
            unique_hackathons[dedupe_key] = hack
    
    final_data = {
        "total_count": len(unique_hackathons),
        "generated_date": datetime.now().strftime("%Y-%m-%d"),
        "generated_at": datetime.now().isoformat(),
        "hack_count": len(unique_hackathons),
        "hackathons": list(unique_hackathons.values()),
        "processing_summary": rounds_summary
    }
    
    # Save to final file
    try:
        final_file = Path("final_results.json")
        with open(final_file, "w", encoding="utf-8") as f:
            json.dump(final_data, f, indent=2, ensure_ascii=False)
        
        print(f"\n✅ Generated {len(unique_hackathons)} unique hackathons")
        print(f"📁 Saved to: {final_file}")
        print(f"[JSON] Parse success - all files processed")
    except Exception as e:
        print(f"\n❌ Error saving final results: {e}")

    # Export additional files
    try:
        government_file = Path("final_governmenthacks.json")
        with open(government_file, "w", encoding="utf-8") as f:
            json.dump({"government_hackathons": list(unique_hackathons.values())}, f, indent=2, ensure_ascii=False)
        print(f"📁 Saved to: {government_file}")
    except Exception as e:
        print(f"\n❌ Error saving final_governmenthacks.json: {e}")

    try:
        archive_dir = Path("V1") / "output" / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        hackathon_ready_file = archive_dir / "hackathon_ready4db.json"
        with open(hackathon_ready_file, "w", encoding="utf-8") as f:
            json.dump({"hackathons": list(unique_hackathons.values())}, f, indent=2, ensure_ascii=False)
        print(f"📁 Saved to: {hackathon_ready_file}")
    except Exception as e:
        print(f"\n❌ Error saving hackathon_ready4db.json: {e}")
    
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
parser.add_argument("--model", default=MODEL, help="Model alias to use for the scraper run.")
args = parser.parse_args()

REQUESTED_MODEL = args.model or MODEL
MODEL = resolve_model_alias(REQUESTED_MODEL)

if args.clear:
    print("🗑️  Clearing previous outputs...")
    clear_previous_outputs()

print(f"\n{'='*80}")
print(f"🚀 GOVERNMENT HACKATHON SCRAPER")
if REQUESTED_MODEL != MODEL:
    print(f"Model: {REQUESTED_MODEL} -> {MODEL} | Rounds: {TOTAL_ROUNDS}")
else:
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
    
    # Build prompt
    if round_num == 1:
        user_prompt = """
Find currently ACTIVE Indian government hackathons,
innovation challenges, AI challenges, coding competitions,
cybersecurity competitions, and defence innovation challenges.

Return STRICT JSON ONLY.
"""
    else:
        previous_json = "\n\n".join(previous_outputs)
        user_prompt = f"""
Previously discovered opportunities:

{previous_json}

Find ONLY NEW active Indian government hackathons
NOT already present above.

Avoid duplicates.
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
                if "candidates" in extracted_json:
                    candidate_count = len(extracted_json["candidates"])
                elif "government_hackathons" in extracted_json:
                    candidate_count = len(extracted_json["government_hackathons"])
                if "excluded" in extracted_json:
                    excluded_count = len(extracted_json.get("excluded") or [])
            elif isinstance(extracted_json, list):
                candidate_count = len(extracted_json)

            print(f"✅ Extracted JSON: {json_output_file} ({candidate_count} candidates)")
            if excluded_count > 0:
                print(f"[JSON] Excluded count: {excluded_count}")
            print(f"[JSON] Candidates found: {candidate_count}")
            previous_outputs.append(output_text)
        else:
            # Fallback: save empty structure
            with open(json_output_file, "w", encoding="utf-8") as f:
                json.dump({"candidates": []}, f, indent=2)

            with open(final_snapshot_file, "w", encoding="utf-8") as f:
                json.dump({"candidates": []}, f, indent=2)

            print(f"⚠️  No JSON extracted from round {round_num} output")
            print(f"[JSON] Parse failed - saved empty candidates")
            previous_outputs.append("{}")

    except Exception as e:
        print(f"\n❌ Error saving round {round_num}: {e}")
        try:
            with open(json_output_file, "w", encoding="utf-8") as f:
                json.dump({"candidates": []}, f, indent=2)
            with open(final_snapshot_file, "w", encoding="utf-8") as f:
                json.dump({"candidates": []}, f, indent=2)
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
print(f"\n✨ Scraper Complete! Generated {final_data['hack_count']} active government hackathons\n")
