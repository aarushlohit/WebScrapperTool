#!/usr/bin/env python3
"""
Generate hackathons_results.json from cached scraper data
"""
import json
from pathlib import Path

from generate_final_cleared import build_final_cleared_file
from search_hackathons import JsonExtractor, NormalizationEngine

def _latest_completed_run(cache_dir: Path) -> Path | None:
    if not cache_dir.exists():
        return None
    completed_runs = [path for path in cache_dir.glob("*/") if (path / "run_summary.json").exists()]
    if not completed_runs:
        return None
    return max(completed_runs, key=lambda path: ((path / "run_summary.json").stat().st_mtime, path.name))


# Create minimal output from cached files
cache_dir = Path(".opportunity_cache/runs")
latest_run = _latest_completed_run(cache_dir)

if latest_run and latest_run.exists():
    print(f"Using cache from: {latest_run.name}")
    payloads = []
    models_attempted = set()
    extractor = JsonExtractor()

    # Load artifact metadata and parse the referenced raw model outputs from the
    # latest completed run only. The per-task .json files are metadata, not the
    # candidate payloads themselves.
    for cache_file in sorted(latest_run.glob("*.json")):
        try:
            if cache_file.name == "run_summary.json":
                continue
            with cache_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    if data.get("model"):
                        models_attempted.add(str(data["model"]))
                    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
                    if metadata.get("discovery_model"):
                        models_attempted.add(str(metadata["discovery_model"]))
                    raw_output_path = data.get("raw_output_path")
                    if isinstance(raw_output_path, str) and raw_output_path:
                        raw_path = Path(raw_output_path)
                        if raw_path.exists():
                            raw_text = raw_path.read_text(encoding="utf-8")
                            parsed, _warnings = extractor.parse_payload(raw_text)
                            if parsed:
                                payloads.append(parsed)
        except Exception as e:
            print(f"Warning: Could not load {cache_file.name}: {e}")

    normalizer = NormalizationEngine(
        current_date="2026-05-10",
        min_confidence=72,
        live_validation=False,
    )
    output = normalizer.normalize_payloads(payloads)
    output["metadata"]["timestamp"] = latest_run.name
    output["metadata"]["models_attempted"] = sorted(models_attempted)
    
    # Save output
    output_file = Path("hackathons_results.json")
    temp_file = output_file.with_suffix(output_file.suffix + ".tmp")
    with temp_file.open("w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
        f.write("\n")
    temp_file.replace(output_file)
    try:
        build_final_cleared_file(output_file, Path("hackathons_final_cleared.json"))
    except Exception as e:
        print(f"Warning: Could not build final cleared JSON: {e}")
    
    print(f"✓ Generated output file: {output_file}")
    print(f"  Government hackathons: {len(output['government_hackathons'])}")
    print(f"  Excluded opportunities: {len(output['excluded_opportunities'])}")
    print(f"✓ SUCCESS - Output written to {output_file}")
else:
    print("✗ No cache found, cannot generate output")
