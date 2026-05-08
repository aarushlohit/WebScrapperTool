import json

with open('hackathons_results.json') as f:
    results = json.load(f)

gov = results.get('government_hackathons', [])
borderline = results.get('borderline_opportunities', [])

print("\n" + "="*80)
print("COMPREHENSIVE SCRAPER ANALYSIS")
print("="*80)

print(f"\n📊 EXECUTIVE SUMMARY")
print(f"✓ Total Government Hackathons: {len(gov)}")
print(f"⚠ Borderline Opportunities: {len(borderline)}")
print(f"✓ Total Entries: {len(gov) + len(borderline)}")

print("\n" + "="*80)
print("VALIDATED HACKATHONS (High Confidence)")
print("="*80)

for i, h in enumerate(gov, 1):
    print(f"\n[{i}] {h.get('hackathon_name', 'N/A')}")
    print(f"    ID: {h.get('id', 'N/A')}")
    print(f"    Ministry: {h.get('ministry', 'N/A')}")
    print(f"    Type: {h.get('event_type', 'N/A')}")
    print(f"    Domain: {h.get('domain', 'N/A')}")
    print(f"    Deadline: {h.get('deadline', 'N/A')}")
    print(f"    Confidence: {h.get('confidence_score', 0)}/100")
    print(f"    Status: {h.get('current_status', 'N/A')}")
    print(f"    Registration: {h.get('registration_url', 'N/A')}")
    print(f"    Prize Pool: {h.get('prizes', 'N/A')[:70]}")
    print(f"    Team Size: {h.get('team_size', 'N/A')}")
    print(f"    Submission Mode: {h.get('mode', 'N/A')}")

print("\n" + "="*80)
print("BORDERLINE OPPORTUNITIES (Requires Review)")
print("="*80)

for i, b in enumerate(borderline, 1):
    if isinstance(b, dict):
        print(f"\n[{i}] Name: {b.get('name', b.get('hackathon_name', 'N/A'))}")
        print(f"    Type: {b.get('type', b.get('event_type', 'N/A'))}")
        print(f"    Confidence: {b.get('confidence', b.get('confidence_score', 'N/A'))}")

print("\n" + "="*80)
print("DATA VALIDATION REPORT")
print("="*80)

required_fields = ['hackathon_name', 'deadline', 'registration_url', 'ministry']
for i, h in enumerate(gov, 1):
    missing = [f for f in required_fields if not h.get(f)]
    print(f"Entry {i}: {'✓ Complete' if not missing else 'Missing: ' + ', '.join(missing)}")

print("\n" + "="*80)
print(f"✓ Analysis complete. {len(gov)} active opportunities found.")
print("="*80)
