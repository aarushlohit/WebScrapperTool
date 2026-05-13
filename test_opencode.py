#!/usr/bin/env python3
import subprocess
import json

prompt = """Find 3 Indian government hackathons happening in 2026. Return STRICT JSON with fields: event_type, hackathon_name, deadline, registration_url. Return JSON ONLY."""

print("[Logger] Sending prompt to OpenCode...")
try:
    process = subprocess.Popen(
        ["opencode", "run", "-m", "opencode/big-pickle", "--dangerously-skip-permissions"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False
    )
    
    stdout, stderr = process.communicate(input=prompt.encode(), timeout=60)
    
    print(f"[Logger] Process exited with code: {process.returncode}")
    print(f"[Logger] Stderr length: {len(stderr)} bytes")
    print(f"[Logger] Stdout length: {len(stdout)} bytes")
    print(f"[Logger] First 500 chars of stderr:\n{stderr[:500].decode('utf-8', errors='replace')}")
    print(f"\n[Logger] FULL STDOUT:\n{stdout.decode('utf-8', errors='replace')}")
    
except subprocess.TimeoutExpired:
    process.kill()
    print("[Logger] Process timed out after 60s")
except Exception as e:
    print(f"[Logger] Error: {e}")
