#!/usr/bin/env python3
"""
Quick test to verify OpenCode JSON extraction works correctly.
This tests the fixed extract_json_from_text() function independently.
"""
import json

def extract_json_from_text(text):
    """Extract JSON (object or array) from mixed text output."""
    # Check which comes first: [ or {
    brace_idx = text.find('{')
    bracket_idx = text.find('[')
    
    # Determine which to process first
    if bracket_idx != -1 and (brace_idx == -1 or bracket_idx < brace_idx):
        # Array comes first, try array extraction
        bracket_count = 0
        for i in range(bracket_idx, len(text)):
            if text[i] == '[':
                bracket_count += 1
            elif text[i] == ']':
                bracket_count -= 1
                if bracket_count == 0:
                    try:
                        json_str = text[bracket_idx:i+1]
                        data = json.loads(json_str)
                        # If it's an array of candidates, wrap it
                        if isinstance(data, list) and data and isinstance(data[0], dict):
                            if "hackathon_name" in data[0] or "event_type" in data[0]:
                                return {"candidates": data}
                        return data
                    except json.JSONDecodeError:
                        pass
    
    # Try object extraction
    if brace_idx != -1:
        brace_count = 0
        for i in range(brace_idx, len(text)):
            if text[i] == '{':
                brace_count += 1
            elif text[i] == '}':
                brace_count -= 1
                if brace_count == 0:
                    try:
                        json_str = text[brace_idx:i+1]
                        return json.loads(json_str)
                    except json.JSONDecodeError:
                        pass
    
    return None

# Test cases
tests = [
    {
        "name": "OpenCode Array Output",
        "input": '''```json
[
  {
    "event_type": "hackathon",
    "hackathon_name": "Smart India Hackathon 2026",
    "deadline": "2026-06-15",
    "registration_url": "https://sih.gov.in"
  },
  {
    "event_type": "challenge",
    "hackathon_name": "IDEX Challenge 2026",
    "deadline": "2026-07-01",
    "registration_url": "https://idex.gov.in"
  }
]
```''',
        "expect_candidates": 2,
        "expect_key": "Smart India Hackathon 2026"
    },
    {
        "name": "Object Format (Fallback)",
        "input": '''Some preamble...
{
  "candidates": [
    {
      "hackathon_name": "AI Challenge 2026",
      "deadline": "2026-05-31"
    }
  ]
}
More text...''',
        "expect_candidates": 1,
        "expect_key": "AI Challenge 2026"
    }
]

print("=" * 70)
print("JSON EXTRACTION TEST")
print("=" * 70)

all_passed = True

for test in tests:
    print(f"\n[TEST] {test['name']}")
    result = extract_json_from_text(test["input"])
    
    if result is None:
        print("  ✗ FAILED: No JSON extracted")
        all_passed = False
        continue
    
    # Check if has candidates
    if "candidates" not in result:
        print(f"  ✗ FAILED: No 'candidates' key in result: {list(result.keys())}")
        all_passed = False
        continue
    
    candidates = result["candidates"]
    if len(candidates) != test["expect_candidates"]:
        print(f"  ✗ FAILED: Expected {test['expect_candidates']} candidates, got {len(candidates)}")
        all_passed = False
        continue
    
    # Check for expected hackathon name
    names = [c.get("hackathon_name", "") for c in candidates]
    if test["expect_key"] not in names:
        print(f"  ✗ FAILED: Expected hackathon '{test['expect_key']}' not found")
        print(f"    Found: {names}")
        all_passed = False
        continue
    
    print(f"  ✓ PASSED")
    print(f"    - Extracted {len(candidates)} candidates")
    print(f"    - Found: {', '.join(names)}")

print("\n" + "=" * 70)
if all_passed:
    print("✓ ALL TESTS PASSED - JSON extraction is working correctly!")
else:
    print("✗ SOME TESTS FAILED")
print("=" * 70)
