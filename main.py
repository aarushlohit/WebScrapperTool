#!/usr/bin/env python3
"""
Indian Government Hackathon Intelligence Engine
Uses OpenCode AI SDK (Local Server) with HY3 preview model for web search
"""

from opencode_ai import Opencode
import json
import sys
import time


def load_system_prompt():
    """Load the system prompt from systemprompt.md"""
    with open("systemprompt.md", "r") as f:
        return f.read()


def check_opencode_server():
    """Check if OpenCode local server is running"""
    try:
        client = Opencode()
        # Try to get providers to verify connection
        print("🔌 Connecting to OpenCode local server at http://localhost:54321...")
        
        # If we can create a client without errors, server is reachable
        return client
    except Exception as e:
        print(f"❌ Cannot connect to OpenCode server!")
        print(f"   Error: {str(e)}")
        print("\n📝 OpenCode runs a LOCAL server on port 54321.")
        print("   Make sure OpenCode IDE/application is running.")
        print("\n   You can download OpenCode from: https://opencode.ai")
        sys.exit(1)


def search_indian_government_hackathons():
    """
    Use OpenCode AI with HY3 model for web search discovery
    """
    # Verify server connection
    client = check_opencode_server()
    
    # Load the system prompt
    system_prompt = load_system_prompt()
    
    # User query for hackathon discovery
    user_query = """Search exhaustively across the entire public web for ALL active and upcoming Indian government hackathons, innovation challenges, and competitions. 

Focus on:
- Central government ministries and departments
- State government portals  
- Academic institutions (IITs, NITs, IIITs)
- Government innovation ecosystems
- Official challenge platforms

Return STRICT VALID JSON ONLY with all discovered opportunities."""
    
    print("\n🔍 Searching for Indian Government Hackathons using HY3 Preview Model...")
    print("=" * 70)
    
    try:
        # Create a new session for the hackathon search
        print("📝 Creating new session...")
        session = client.session.create()
        session_id = session.id
        print(f"✓ Session created: {session_id}\n")
        
        # Send the search query using HY3 model with web search
        print("📡 Sending search query to HY3 model with web search enabled...")
        
        # Prepare the message parts - just text content
        parts = [{"type": "text", "text": user_query}]
        
        # Make the API call to HY3 model
        response = client.session.chat(
            id=session_id,
            model_id="hy3-preview",  # HY3 Preview Model
            provider_id="opencode",  # OpenCode provider
            parts=parts,
            system=system_prompt,  # Include the system prompt here
            mode="search",  # Enable search mode for web search
            tools={"web_search": True}  # Enable web search tool
        )
        
        print("\n📊 SEARCH RESULTS:\n")
        
        # Extract and display the response
        if hasattr(response, 'content'):
            result_text = response.content
        elif hasattr(response, 'text'):
            result_text = response.text
        else:
            result_text = str(response)
        
        # Try to parse as JSON
        try:
            import re
            json_match = re.search(r'\{[\s\S]*\}', result_text)
            if json_match:
                json_str = json_match.group(0)
                result_json = json.loads(json_str)
                
                # Pretty print the JSON
                print(json.dumps(result_json, indent=2, ensure_ascii=False))
                
                # Print summary
                if "metadata" in result_json:
                    metadata = result_json["metadata"]
                    print("\n" + "=" * 70)
                    print("📈 SUMMARY:")
                    print(f"   Total Active Hackathons: {metadata.get('total_active_hackathons', 'N/A')}")
                    print(f"   Search Date: {metadata.get('search_date', 'N/A')}")
                    print(f"   Sources Scanned: {len(metadata.get('sources_scanned', []))}")
                    if metadata.get('notes'):
                        print(f"   Notes: {metadata.get('notes')}")
                    print("=" * 70)
            else:
                print(result_text)
        except json.JSONDecodeError:
            print(result_text)
        
        print("\n✅ Search completed successfully!")
        
        # Clean up - delete session
        try:
            client.session.delete(session_id)
            print(f"🧹 Session {session_id} cleaned up")
        except:
            pass
        
        return response
        
    except Exception as e:
        print(f"❌ Error during web search: {str(e)}")
        import traceback
        traceback.print_exc()
        raise


if __name__ == "__main__":
    search_indian_government_hackathons()