#!/usr/bin/env python3
"""
Indian Government Hackathon Discovery using OpenCode CLI
Executes system prompt via OpenCode CLI with web search enabled
"""

import subprocess
import json
import re
import sys
import argparse

PROJECT_DIR = "/home/aarush/Myoffice/CodeLab Projects/WebSearch-Agent"
OUTPUT_JSON = "hackathons_results.json"
# Default priority:
# 1. OpenCode Big Pickle
# 2. OpenCode HY3 Preview Free
# 3. Cloudflare Workers AI Kimi K2.6
MODELS = [
    "opencode/big-pickle",
    "opencode/hy3-preview-free",
    "cloudflare-workers-ai/@cf/moonshotai/kimi-k2.6",
]

MODEL_ALIASES = {
    "big-pickle": "opencode/big-pickle",
    "big_pickle": "opencode/big-pickle",
    "pickle": "opencode/big-pickle",
    "hy3": "opencode/hy3-preview-free",
    "hy3-preview": "opencode/hy3-preview-free",
    "hy3-preview-free": "opencode/hy3-preview-free",
    "kimi": "cloudflare-workers-ai/@cf/moonshotai/kimi-k2.6",
    "kimi-2.6": "cloudflare-workers-ai/@cf/moonshotai/kimi-k2.6",
    "kimi-k2.6": "cloudflare-workers-ai/@cf/moonshotai/kimi-k2.6",
    "cloudflare": "cloudflare-workers-ai/@cf/moonshotai/kimi-k2.6",
}


def parse_args():
    """Parse command-line options."""
    parser = argparse.ArgumentParser(
        description="Search for active Indian government hackathons via OpenCode."
    )
    parser.add_argument(
        "-m",
        "--model",
        help=(
            "Run only one model. Accepts full model IDs or aliases: "
            "big-pickle, hy3, kimi, cloudflare."
        ),
    )
    return parser.parse_args()


def resolve_model(model):
    """Resolve a friendly model alias to the OpenCode model ID."""
    if not model:
        return None
    return MODEL_ALIASES.get(model.strip().lower(), model.strip())


def load_system_prompt():
    """Load the system prompt from systemprompt.md"""
    try:
        with open("systemprompt.md", "r") as f:
            return f.read()
    except FileNotFoundError:
        print("❌ systemprompt.md not found!")
        sys.exit(1)


def run_opencode_search(model, query):
    """Run opencode with a specific model and return combined output."""
    print(f"🌐 Using OpenCode CLI with Web Search")
    print(f"   Model: {model}")
    print("⏳ This may take 1-2 minutes for exhaustive search...\n")

    result = subprocess.run(
        ["opencode", "run", "-m", model, query],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=300  # 5 minute timeout for web search
    )

    output = result.stdout + result.stderr
    if result.returncode != 0:
        raise RuntimeError(
            f"OpenCode failed with exit code {result.returncode}.\n{output}"
        )

    # OpenCode sometimes prints provider/init errors but still exits 0.
    # Detect these so we can fall back to the next model.
    error_patterns = [
        r"ProviderInitError",
        r"AI_APICallError",
        r"AI_LoadAPIKeyError",
        r"AI_RetryError",
        r"NoSuchModelError",
        r"InvalidModelError",
    ]
    for pattern in error_patterns:
        if re.search(pattern, output):
            raise RuntimeError(
                f"OpenCode reported a model error ({pattern}). Output:\n{output.strip()}"
            )

    # Also treat output that is essentially just an "Error:" line as a failure.
    non_banner_lines = [
        ln for ln in output.strip().splitlines()
        if ln.strip() and not ln.lstrip().startswith(">")
    ]
    if non_banner_lines and all(
        ln.lstrip().lower().startswith("error") for ln in non_banner_lines
    ):
        raise RuntimeError(
            f"OpenCode produced only error output:\n{output.strip()}"
        )

    return output


def extract_json(output):
    """Extract and parse the first complete JSON object or array from output."""
    json_match = re.search(r'(\[[\s\S]*\]|\{[\s\S]*\})', output)
    if not json_match:
        return None

    json_str = json_match.group(0)

    # Find the proper closing bracket/brace for JSON arrays or objects
    brace_count = 0
    bracket_count = 0
    end_pos = 0

    for i, char in enumerate(json_str):
        if char == '{':
            brace_count += 1
        elif char == '}':
            brace_count -= 1
        elif char == '[':
            bracket_count += 1
        elif char == ']':
            bracket_count -= 1

        # When all braces/brackets are closed, we have complete JSON
        if (brace_count == 0 and bracket_count == 0) and i > 0:
            end_pos = i + 1
            break

    if end_pos > 0:
        json_str = json_str[:end_pos]

    return json.loads(json_str)


def export_json(result_json):
    """Export parsed search results to a JSON file."""
    with open(OUTPUT_JSON, "w") as f:
        json.dump(result_json, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"\n💾 Exported JSON results to: {OUTPUT_JSON}")


def print_summary(result_json):
    """Print a compact discovery summary for parsed results."""
    if isinstance(result_json, list):
        print("\n" + "=" * 70)
        print("📈 DISCOVERY SUMMARY:")
        print(f"   Total Active Hackathons Found: {len(result_json)}")
        for i, hackathon in enumerate(result_json, 1):
            print(f"\n   {i}. {hackathon.get('name', 'Unknown')}")
            print(f"      Status: {hackathon.get('status', 'N/A')}")
            if 'official_url' in hackathon:
                print(f"      URL: {hackathon['official_url']}")
        print("\n" + "=" * 70)
    elif isinstance(result_json, dict) and "metadata" in result_json:
        metadata = result_json["metadata"]
        print("\n" + "=" * 70)
        print("📈 DISCOVERY SUMMARY:")
        print(f"   Total Active Hackathons: {metadata.get('total_active_hackathons', 'N/A')}")
        print(f"   Search Date: {metadata.get('search_date', 'N/A')}")
        print(f"   Sources Scanned: {len(metadata.get('sources_scanned', []))}")
        print("=" * 70)


def search_using_opencode_cli(models=None):
    """
    Use OpenCode CLI directly to search for Indian government hackathons
    """
    system_prompt = load_system_prompt()
    
    # Create the search query
    query = f"""{system_prompt}

Using the detailed system prompt above, search exhaustively for ALL active Indian government hackathons.

Return ONLY valid JSON matching the schema specified in the system prompt."""
    
    print("🔍 Searching for Indian Government Hackathons and Innovation Challenges...")
    print("=" * 70)
    models = models or MODELS
    if len(models) == 1:
        print(f"🎯 Selected model: {models[0]}\n")
    else:
        print("🥇 First priority: OpenCode Big Pickle")
        print("🛟 Fallback order: OpenCode HY3 Preview Free, Cloudflare Workers AI Kimi K2.6\n")
    
    last_error = None

    for idx, model in enumerate(models):
        try:
            output = run_opencode_search(model, query)
            break
        except subprocess.TimeoutExpired as e:
            last_error = e
            print(f"⏱️  Search timed out after 5 minutes with {model}.")
        except Exception as e:
            last_error = e
            print(f"⚠️  Error with {model}: {str(e)}")

        if idx < len(models) - 1:
            print(f"\n🔁 Falling back to {models[idx + 1]}...\n")

    else:
        # The for-loop completed without `break` -> every model failed.
        print("❌ All configured models failed.")
        if last_error:
            raise last_error
        return None
        
    print("\n📊 RAW OUTPUT FROM OPENCODE:\n")
    print(output)

    # Try to extract JSON from output
    print("\n" + "=" * 70)
    print("🔎 Parsing Results...\n")

    try:
        result_json = extract_json(output)
        if result_json is None:
            print("ℹ️  No JSON structure found in output.")
            print("This may indicate the search is still processing or formatting results differently.")
            return output

        print("✅ Valid JSON found:\n")
        print(json.dumps(result_json, indent=2, ensure_ascii=False))
        export_json(result_json)
        print_summary(result_json)
    except json.JSONDecodeError as e:
        print(f"⚠️  Could not parse JSON: {e}")
        print("But the raw search results are shown above.")

    return output


if __name__ == "__main__":
    args = parse_args()
    selected_model = resolve_model(args.model)
    search_using_opencode_cli([selected_model] if selected_model else None)
