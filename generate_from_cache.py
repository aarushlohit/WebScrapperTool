#!/usr/bin/env python3
"""
Generate hackathons_results.json from cached scraper data
"""
import json
from pathlib import Path

# Create minimal output from cached files
cache_dir = Path(".opportunity_cache/runs")
latest_run = sorted(cache_dir.glob("*/"), key=lambda x: x.name)[-1] if cache_dir.exists() else None

if latest_run and latest_run.exists():
    print(f"Using cache from: {latest_run.name}")
    gov_hackathons = []
    borderline = []
    
    # Load all cached JSON files from latest run
    for cache_file in sorted(latest_run.glob("*.json")):
        try:
            with open(cache_file) as f:
                data = json.load(f)
                if isinstance(data, dict) and 'candidates' in data:
                    for item in data.get('candidates', []):
                        if isinstance(item, dict):
                            # Extract hackathon info
                            confidence = item.get('confidence_score', item.get('confidence', 0))
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
            "date_searched": "2026-05-08"
        }
    }
    
    # Save output
    output_file = Path("hackathons_results.json")
    with open(output_file, "w") as f:
        json.dump(output, f, indent=2)
    
    print(f"✓ Generated output file: {output_file}")
    print(f"  Government hackathons: {len(output['government_hackathons'])}")
    print(f"  Borderline opportunities: {len(output['borderline_opportunities'])}")
    print(f"✓ SUCCESS - Output written to {output_file}")
else:
    print("✗ No cache found, cannot generate output")
