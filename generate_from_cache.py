#!/usr/bin/env python3
"""
Generate hackathons_results.json from cached scraper data
"""
import json
from pathlib import Path

from coverage_intelligence import CoverageIntelligenceEngine, SearchSaturationTracker, attach_consensus_to_event

# Create minimal output from cached files
cache_dir = Path(".opportunity_cache/runs")
latest_run = sorted(cache_dir.glob("*/"), key=lambda x: x.name)[-1] if cache_dir.exists() else None

if latest_run and latest_run.exists():
    print(f"Using cache from: {latest_run.name}")
    gov_hackathons = []
    borderline = []
    payloads = []
    models_attempted = set()
    
    # Load all cached JSON files from latest run
    for cache_file in sorted(latest_run.glob("*.json")):
        try:
            with cache_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    payloads.append(data)
                    if data.get("model"):
                        models_attempted.add(str(data["model"]))
                    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
                    if metadata.get("discovery_model"):
                        models_attempted.add(str(metadata["discovery_model"]))
                if isinstance(data, dict) and 'candidates' in data:
                    for item in data.get('candidates', []):
                        if isinstance(item, dict):
                            # Extract hackathon info
                            confidence = item.get('confidence_score', item.get('confidence', 0))
                            attach_consensus_to_event(item)
                            if confidence >= 80:
                                gov_hackathons.append(item)
                            else:
                                borderline.append(item)
        except Exception as e:
            print(f"Warning: Could not load {cache_file.name}: {e}")
    
    # Remove duplicates by ID
    seen_ids = set()
    unique_gov = []
    for h in gov_hackathons:
        hid = h.get('id', h.get('hackathon_name', ''))
        if hid and hid not in seen_ids:
            unique_gov.append(h)
            seen_ids.add(hid)
    
    seen_ids = set()
    unique_borderline = []
    for b in borderline:
        bid = b.get('id', b.get('name', ''))
        if bid and bid not in seen_ids:
            unique_borderline.append(b)
            seen_ids.add(bid)
    
    # Create output structure
    output = {
        "government_hackathons": unique_gov,
        "borderline_opportunities": unique_borderline,
        "metadata": {
            "timestamp": latest_run.name,
            "total_tasks": len(list(latest_run.glob("*.json"))),
            "successful_tasks": len(list(latest_run.glob("*.json"))) - 1,
            "total_candidates": len(unique_gov) + len(unique_borderline),
            "total_active_hackathons": len(unique_gov),
            "total_borderline_opportunities": len(unique_borderline),
            "total_excluded": 0,
            "date_searched": "2026-05-08"
        }
    }

    saturation_tracker = SearchSaturationTracker()
    saturation_tracker.record_query_batch(
        queries=["cache_generation"],
        new_events_discovered=len(unique_gov),
        duplicate_events_discovered=0,
        rejected_events_discovered=0,
    )
    coverage_engine = CoverageIntelligenceEngine(Path("coverage_history.json"))
    coverage_analysis = coverage_engine.analyze(
        final_payload=output,
        discovery_tasks=[],
        payloads=payloads,
        artifacts=[],
        saturation_tracker=saturation_tracker,
        models_attempted=sorted(models_attempted),
    )
    output["metadata"]["coverage_analysis"] = coverage_analysis
    output["metadata"]["coverage_confidence"] = coverage_analysis["coverage_confidence"]
    output["metadata"]["coverage_status"] = coverage_analysis["coverage_status"]
    
    # Save output
    output_file = Path("hackathons_results.json")
    temp_file = output_file.with_suffix(output_file.suffix + ".tmp")
    with temp_file.open("w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
        f.write("\n")
    temp_file.replace(output_file)
    coverage_engine.persist_history(output)
    
    print(f"✓ Generated output file: {output_file}")
    print(f"  Government hackathons: {len(output['government_hackathons'])}")
    print(f"  Borderline opportunities: {len(output['borderline_opportunities'])}")
    print(f"✓ SUCCESS - Output written to {output_file}")
else:
    print("✗ No cache found, cannot generate output")
