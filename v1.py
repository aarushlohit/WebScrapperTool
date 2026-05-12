
import subprocess
import json
import re
import argparse
import time
from pathlib import Path
from datetime import datetime

MODEL = "opencode/big-pickle"
SYSTEM_PROMPT_FILE = "systemprompt.md"
TOTAL_ROUNDS = 3

OUTPUT_DIR = Path("round_outputs")
OUTPUT_DIR.mkdir(exist_ok=True)


def clear_previous_outputs():
    """Remove previous round outputs and the final consolidated file."""
    for file_path in OUTPUT_DIR.glob("round*_raw.json"):
        file_path.unlink(missing_ok=True)

    final_file = Path("final_governmenthacks.json")
    final_file.unlink(missing_ok=True)

# =========================
# JSON EXTRACTION & FILTERING
# =========================

def extract_json_from_text(text):
    """Extract JSON object from mixed text output by finding balanced braces."""
    start_idx = text.find('{')
    if start_idx == -1:
        return None
    
    brace_count = 0
    for i in range(start_idx, len(text)):
        if text[i] == '{':
            brace_count += 1
        elif text[i] == '}':
            brace_count -= 1
            if brace_count == 0:
                try:
                    json_str = text[start_idx:i+1]
                    return json.loads(json_str)
                except json.JSONDecodeError:
                    return None
    return None

def filter_by_deadline(candidates, today_str="2026-05-12"):
    """Filter candidates with deadline greater than today."""
    today = datetime.strptime(today_str, "%Y-%m-%d")
    filtered = []
    
    for candidate in candidates:
        deadline_str = candidate.get("deadline")
        if deadline_str:
            try:
                deadline = datetime.strptime(deadline_str, "%Y-%m-%d")
                if deadline > today:
                    filtered.append(candidate)
            except ValueError:
                # Skip if date format is invalid
                pass
    
    return filtered

def process_rounds_and_generate_final():
    """Process all round outputs and generate final JSON with active hackathons."""
    all_active = []
    
    for round_num in range(1, TOTAL_ROUNDS + 1):
        output_file = OUTPUT_DIR / f"round{round_num}_raw.json"
        
        with open(output_file, "r", encoding="utf-8") as f:
            content = f.read()
        
        # Extract JSON from mixed output
        data = extract_json_from_text(content)
        
        if data and "candidates" in data:
            candidates = data.get("candidates", [])
            
            # Filter for active status and future deadlines
            active_candidates = [
                c for c in candidates 
                if c.get("current_status") == "active"
            ]
            
            # Filter by deadline
            shortlisted = filter_by_deadline(active_candidates)
            all_active.extend(shortlisted)
            
            print(f"Round {round_num}: Found {len(candidates)} total, "
                  f"{len(active_candidates)} active, "
                  f"{len(shortlisted)} with future deadlines")
    
    # Remove duplicates by hackathon_name
    unique_hackathons = {}
    for hack in all_active:
        name = hack.get("hackathon_name")
        if name and name not in unique_hackathons:
            unique_hackathons[name] = hack
    
    final_data = {
        "total_count": len(unique_hackathons),
        "generated_date": datetime.now().strftime("%Y-%m-%d"),
        "hack_count": len(unique_hackathons),
        "hackathons": list(unique_hackathons.values())
    }
    
    # Save to final file
    final_file = Path("final_governmenthacks.json")
    with open(final_file, "w", encoding="utf-8") as f:
        json.dump(final_data, f, indent=2, ensure_ascii=False)
    
    print(f"\n✅ Generated: {final_file}")
    print(f"📊 Total active hackathons with future deadlines: {len(unique_hackathons)}")
    
    return final_data


# =========================
# LOAD SYSTEM PROMPT
# =========================

with open(SYSTEM_PROMPT_FILE, "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()

previous_outputs = []

parser = argparse.ArgumentParser(description="Run OpenCode rounds and consolidate output.")
parser.add_argument(
    "--clear",
    action="store_true",
    help="Clear previous round outputs and the final JSON before running.",
)
args = parser.parse_args()

if args.clear:
    clear_previous_outputs()

run_started_at = time.perf_counter()

for round_num in range(1, TOTAL_ROUNDS + 1):
    round_started_at = time.perf_counter()

    print(f"\n{'=' * 80}")
    print(f"🚀 ROUND {round_num}")
    print(f"{'=' * 80}")

    # =========================
    # BUILD USER PROMPT
    # =========================

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

    full_prompt = f"""
{SYSTEM_PROMPT}

{user_prompt}
"""

    # =========================
    # RUN OPENCODE
    # =========================

    cmd = [
        "opencode",
        "run",
        "-m",
        MODEL,
        full_prompt
    ]

    print("⚡ Running OpenCode...\n")

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )

    output_lines = []

    # =========================
    # LIVE TERMINAL STREAM
    # =========================

    for line in process.stdout:
        print(line, end="")   # LIVE RAW OUTPUT
        output_lines.append(line)

    process.wait()

    output_text = "".join(output_lines).strip()

    # =========================
    # SAVE RAW OUTPUT
    # =========================

    output_file = OUTPUT_DIR / f"round{round_num}_raw.json"

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(output_text)

    print(f"\n✅ Saved: {output_file}")
    print(f"⏱️  Round {round_num} took {time.perf_counter() - round_started_at:.2f}s")

    previous_outputs.append(output_text)

print("\n🎯 ALL ROUNDS COMPLETED")

# =========================
# PROCESS AND GENERATE FINAL JSON
# =========================

final_data = process_rounds_and_generate_final()

total_elapsed = time.perf_counter() - run_started_at
print(f"⏱️  Total runtime: {total_elapsed:.2f}s")



