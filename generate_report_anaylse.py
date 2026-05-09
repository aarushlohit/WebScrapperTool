#!/usr/bin/env python3
import json
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent
INPUT = ROOT / "hackathons_results.json"
OUTPUT = ROOT / "report_anaylse.md"

if not INPUT.exists():
    print("Input JSON not found:", INPUT)
    raise SystemExit(1)

with INPUT.open(encoding="utf-8") as f:
    data = json.load(f)

meta = data.get("metadata", {})
lines = []
lines.append("# Hackathons Analysis Report")
lines.append("")
lines.append(f"Generated: {datetime.utcnow().isoformat()} UTC")
lines.append("")
lines.append("## Summary")
lines.append("")
lines.append(f"- Search date: {meta.get('search_date', 'n/a')}")
lines.append(f"- Validation date: {meta.get('current_date_used_for_validation', 'n/a')}")
lines.append(f"- Total candidates discovered: {meta.get('total_candidates_discovered', 0)}")
lines.append(f"- Active hackathons: {meta.get('total_active_hackathons', 0)}")
lines.append(f"- Fully verified: {meta.get('total_fully_verified', 0)}")
lines.append(f"- Likely active: {meta.get('total_likely_active', 0)}")
lines.append(f"- Borderline: {meta.get('total_borderline_opportunities', 0)}")
lines.append(f"- Excluded: {meta.get('total_excluded', 0)}")
lines.append("")

# Top domains
sources = meta.get('sources_scanned', [])
lines.append('## Top Domains Scanned')
for domain in sorted(set(sources))[:20]:
    lines.append(f"- {domain}")
lines.append("")

# List top active hackathons
lines.append('## Active Hackathons (sample)')
active = data.get('government_hackathons') or []
for idx, item in enumerate(active[:20], 1):
    name = item.get('hackathon_name') or item.get('full_name') or item.get('name')
    url = item.get('registration_url') or item.get('source_url') or item.get('official_event_page') or item.get('official_website')
    deadline = item.get('deadline') or item.get('application_deadline') or item.get('submission_close_date')
    conf = item.get('confidence_score')
    tier = item.get('classification_tier') or item.get('verification_state')
    lines.append(f"{idx}. **{name}** — {tier} — Confidence: {conf} — Deadline: {deadline}  \n    Source: {url}")

lines.append("")
lines.append('## Notes')
lines.append("")
lines.append('- This report is auto-generated from `hackathons_results.json`.')
lines.append('- Excluded items (archived/startup/funding) are omitted from active lists.')

OUTPUT.write_text('\n'.join(lines), encoding='utf-8')
print('Wrote', OUTPUT)
