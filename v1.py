
import subprocess
import json
import re
import argparse
import os
import select
import time
import traceback
from pathlib import Path
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.text import Text
from rich.table import Table

MODEL = "opencode/big-pickle"
SYSTEM_PROMPT_FILE = "systemprompt.md"
TOTAL_ROUNDS = int(os.environ.get("TOTAL_ROUNDS", "3"))
OPENCODE_IDLE_TIMEOUT_SECONDS = int(os.environ.get("OPENCODE_IDLE_TIMEOUT_SECONDS", "1200"))

OUTPUT_DIR = Path("round_outputs")
OUTPUT_DIR.mkdir(exist_ok=True)

console = Console()

def log(message, style="cyan"):
    """Log with minimal styling."""
    console.print(f"  {message}", style=style)


def clear_previous_outputs():
    """Remove previous round outputs and the final consolidated file."""
    removed = 0
    for file_path in OUTPUT_DIR.glob("round*_raw.json"):
        file_path.unlink(missing_ok=True)
        removed += 1

    final_file = Path("final_results.json")
    final_file.unlink(missing_ok=True)
    log(f"Cleared {removed} round output file(s) + final_results.json", style="green")

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


def is_junk_event(candidate):
    text = " ".join([
        str(candidate.get("event_type", "")),
        str(candidate.get("hackathon_name", "")),
        str(candidate.get("full_name", "")),
        str(candidate.get("theme", "")),
        str(candidate.get("domain", "")),
    ]).lower()
    junk_keywords = ["workshop", "webinar", "seminar", "bootcamp", "training", "orientation", "info session"]
    return any(keyword in text for keyword in junk_keywords)

def process_rounds_and_generate_final():
    """Process all round outputs and generate final JSON with active hackathons."""
    all_active = []

    console.print()
    log("Processing round outputs...", style="blue")
    
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
                and not is_junk_event(c)
                and c.get("deadline")
            ]
            
            # Filter by deadline
            shortlisted = filter_by_deadline(active_candidates)
            all_active.extend(shortlisted)
            
            log(
                f"Round {round_num}: total={len(candidates)} active={len(active_candidates)} future={len(shortlisted)}",
                style="cyan"
            )
        else:
            log(f"Round {round_num}: no valid candidates", style="yellow")
    
    # Remove duplicates by multiple canonical identifiers
    unique_hackathons = {}
    for hack in all_active:
        keys = [
            (hack.get("hackathon_name") or "").strip().lower(),
            (hack.get("source_url") or "").strip().lower(),
            (hack.get("official_event_page") or "").strip().lower(),
            (hack.get("registration_url") or "").strip().lower(),
        ]
        dedupe_key = "|".join([key for key in keys if key])
        if dedupe_key and dedupe_key not in unique_hackathons:
            unique_hackathons[dedupe_key] = hack
    
    final_data = {
        "total_count": len(unique_hackathons),
        "generated_date": datetime.now().strftime("%Y-%m-%d"),
        "hack_count": len(unique_hackathons),
        "hackathons": list(unique_hackathons.values())
    }
    
    # Save to final file
    final_file = Path("final_results.json")
    with open(final_file, "w", encoding="utf-8") as f:
        json.dump(final_data, f, indent=2, ensure_ascii=False)
    
    log(f"Generated {len(unique_hackathons)} unique active hackathons", style="green")
    
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
    log("--clear requested: removing previous scraper outputs", style="yellow")
    clear_previous_outputs()

# Print header
console.print()
console.print(Panel(
    f"[cyan]🚀 3-Round Government Hackathon Scraper[/cyan]\n"
    f"[dim]Model: {MODEL} | Timeout: {OPENCODE_IDLE_TIMEOUT_SECONDS}s | Rounds: {TOTAL_ROUNDS}[/dim]",
    style="cyan"
))
console.print()

run_started_at = time.perf_counter()

for round_num in range(1, TOTAL_ROUNDS + 1):
    round_started_at = time.perf_counter()

    console.print(f"[bold cyan]▶ ROUND {round_num}/{TOTAL_ROUNDS}[/bold cyan]")
    console.print()

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
        full_prompt,
        "--dangerously-skip-permissions",
    ]

    log("Launching OpenCode...", style="blue")

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

        output_lines = []
        last_output_at = time.monotonic()

        # =========================
        # LIVE TERMINAL STREAM
        # =========================

        assert process.stdout is not None
        while True:
            ready, _, _ = select.select([process.stdout], [], [], 1.0)

            if ready:
                line = process.stdout.readline()
                if line:
                    print(line, end="", flush=True)
                    output_lines.append(line)
                    last_output_at = time.monotonic()
                elif process.poll() is not None:
                    break
            else:
                if process.poll() is not None:
                    break
                if time.monotonic() - last_output_at > OPENCODE_IDLE_TIMEOUT_SECONDS:
                    process.kill()
                    raise TimeoutError(
                        f"OpenCode timed out after {OPENCODE_IDLE_TIMEOUT_SECONDS} seconds without output"
                    )

        process.wait()

        if process.returncode not in (0, None):
            raise RuntimeError(f"OpenCode exited with code {process.returncode}")

        output_text = "".join(output_lines).strip()
        if not output_text:
            raise RuntimeError("OpenCode returned no output")
    except Exception:
        log("Error: OpenCode execution failed", style="red")
        log(traceback.format_exc().strip(), style="red")
        raise

    # =========================
    # SAVE RAW OUTPUT
    # =========================

    output_file = OUTPUT_DIR / f"round{round_num}_raw.json"

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(output_text)

    log(f"Saved raw output: {output_file.name}", style="green")
    log(f"Round {round_num} completed in {time.perf_counter() - round_started_at:.1f}s", style="dim")
    
    previous_outputs.append(output_text)
    console.print()
log("ALL ROUNDS COMPLETED", style="green")

# =========================
# PROCESS AND GENERATE FINAL JSON
# =========================

final_data = process_rounds_and_generate_final()

total_elapsed = time.perf_counter() - run_started_at

# Print summary
console.print()
table = Table(title="Scraper Summary", show_header=True, header_style="bold cyan", style="cyan")
table.add_column("Metric", style="dim")
table.add_column("Value", style="green")
table.add_row("Total Hackathons", str(final_data["hack_count"]))
table.add_row("Generated Date", final_data["generated_date"])
table.add_row("Total Runtime", f"{total_elapsed:.1f}s")
table.add_row("Output File", "final_results.json")
console.print(table)

console.print()
console.print(Panel(
    f"[green]✅ Scraper Complete![/green]\n"
    f"[cyan]{final_data['hack_count']} active government hackathons[/cyan]",
    style="green"
))
console.print()



