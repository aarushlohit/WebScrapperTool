import json

with open('hackathons_results.json') as f:
    results = json.load(f)

print("\n" + "="*75)
print("SCRAPER OUTPUT ANALYSIS")
print("="*75)

gov = results.get('government_hackathons', [])
print(f"\n✓ Government Hackathons: {len(gov)} entries")
if gov:
    h = gov[0]
    print(f"\n  First Hackathon:")
    print(f"    • Name: {h.get('hackathon_name', 'N/A')}")
    print(f"    • Ministry: {h.get('ministry', 'N/A')}")
    print(f"    • Deadline: {h.get('deadline', 'N/A')}")
    print(f"    • Domain: {h.get('domain', 'N/A')}")
    print(f"    • Confidence: {h.get('confidence_score', 'N/A')}/100")
    print(f"    • Prizes: {h.get('prizes', 'N/A')[:50]}...")
    print(f"    • URL: {h.get('registration_url', 'N/A')}")

borderline = results.get('borderline_opportunities', [])
print(f"\n⚠ Borderline Opportunities: {len(borderline)} entries")

meta = results.get('metadata', {})
print(f"\n📊 METADATA:")
print(f"    • Timestamp: {meta.get('timestamp', 'N/A')}")
print(f"    • Total Tasks: {meta.get('total_tasks', 'N/A')}")
print(f"    • Successful: {meta.get('successful_tasks', 'N/A')}")
print(f"    • Total Candidates: {meta.get('total_candidates', 'N/A')}")

if gov:
    scores = [g.get('confidence_score', 0) for g in gov]
    print(f"\n📈 QUALITY METRICS:")
    print(f"    • Confidence Scores: Min={min(scores)}, Max={max(scores)}, Avg={sum(scores)/len(scores):.1f}")
    
    domains = {}
    for g in gov:
        d = g.get('domain', 'Unknown')
        domains[d] = domains.get(d, 0) + 1
    print(f"    • Domain Distribution: {domains}")

print("\n" + "="*75)
