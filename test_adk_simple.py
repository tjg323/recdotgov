#!/usr/bin/env python3
"""
Simple test to verify ADK agent response interpretation
"""

import json
from adk_agent import recreation_api_tool

def test_analyze_results():
    """Test the analyze_results tool directly"""
    result = recreation_api_tool(
        command="analyze_results",
        location="South Lake Tahoe", 
        distance=10,
        month="2025-08"
    )
    
    print("=== TOOL RESPONSE ===")
    print(f"Status: {result.get('status')}")
    print(f"Has json_output: {'json_output' in result}")
    
    if result.get('json_output'):
        try:
            parsed = json.loads(result['json_output'])
            total_found = parsed.get('total_found', 0)
            print(f"Total found: {total_found}")
            if total_found > 0:
                print(f"Campgrounds: {len(parsed.get('campgrounds', []))}")
                for i, campground in enumerate(parsed.get('campgrounds', [])[:3]):
                    print(f"  {i+1}. {campground.get('name')} ({campground.get('distance')} miles)")
                    print(f"     Sites: {campground.get('site_count', 0)}")
        except json.JSONDecodeError as e:
            print(f"JSON parsing error: {e}")
    
    print("\n=== EXPECTED INTERPRETATION ===")
    if result.get('status') == 'success' and result.get('json_output'):
        try:
            parsed = json.loads(result['json_output'])
            total_found = parsed.get('total_found', 0)
            if total_found > 0:
                print(f"✅ SUCCESS: {total_found} campgrounds found")
                print("✅ Agent should say: 'Great! I found campgrounds with availability!'")
            else:
                print("❌ No campgrounds found")
        except:
            print("❌ Could not parse response")
    else:
        print("❌ Tool failed")

if __name__ == "__main__":
    test_analyze_results()