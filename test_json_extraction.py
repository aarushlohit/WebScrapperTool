import json
import sys
sys.path.insert(0, '.')
from v1 import extract_json_from_text

# Test 1: Array format (what OpenCode returns)
test_array = '''
Some preamble text...
```json
[
  {
    "event_type": "hackathon",
    "hackathon_name": "Test Hackathon",
    "deadline": "2026-05-10",
    "registration_url": "https://example.com"
  }
]
```
Some epilogue text.
'''

# Test 2: Object format (alternative)
test_object = '''
Some text...
{
  "candidates": [
    {
      "hackathon_name": "Another Hackathon",
      "deadline": "2026-06-15"
    }
  ]
}
More text.
'''

print("Test 1 (Array format):")
result1 = extract_json_from_text(test_array)
print(f"  Result: {json.dumps(result1, indent=2)}")

print("\nTest 2 (Object format):")
result2 = extract_json_from_text(test_object)
print(f"  Result: {json.dumps(result2, indent=2)}")

if result1 and "candidates" in result1:
    print("\n✓ Array extraction works!")
else:
    print("\n✗ Array extraction failed!")

if result2 and "candidates" in result2:
    print("✓ Object extraction works!")
else:
    print("✗ Object extraction failed!")
