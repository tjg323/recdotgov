#!/usr/bin/env python3
"""
ADK-based Campground Availability Agent
"""

import os
import json
import asyncio
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
from google.adk.agents import Agent
from google.adk.tools import FunctionTool
from google.adk.runners import InMemoryRunner
from google.adk.sessions import InMemorySessionService
from google.genai import types
import requests
from cache_manager import CacheManager
import subprocess
import time

# Google API key should be set as environment variable
# If not set, ADK features won't work
if "GOOGLE_API_KEY" not in os.environ:
    print("Warning: GOOGLE_API_KEY not set. ADK features will not work.")

def recreation_api_tool(command: str, location: Optional[str] = None, distance: int = 50, month: Optional[str] = None) -> Dict[str, Any]:
    """
    Execute recreation.gov data commands
    
    Args:
        command: The operation to perform (build_campground_list, fetch_availability, check_cache, analyze_results)
        location: Location to search around (required for build_campground_list)
        distance: Distance in miles (default 50)
        month: Month in YYYY-MM format (required for fetch_availability)
    
    Returns:
        Dict with status and results
    """
    try:
        if command == "check_cache":
            # Check cache status
            result = subprocess.run(
                ["python3", "fetch.py", "--cache-status"],
                cwd=os.getcwd(),  # Use current working directory
                capture_output=True,
                text=True,
                timeout=30
            )
            return {
                "status": "success",
                "output": result.stdout,
                "error": result.stderr if result.stderr else None
            }
        
        elif command == "build_campground_list":
            if not location:
                return {"status": "error", "message": "Location is required for building campground list"}
            
            # Build campground list
            cmd = ["python3", "fetch.py", "--build-csv", "--location", location, "--distance", str(distance)]
            result = subprocess.run(
                cmd,
                cwd=os.getcwd(),  # Use current working directory
                capture_output=True,
                text=True,
                timeout=300  # 5 minutes for building campground list
            )
            return {
                "status": "success" if result.returncode == 0 else "error",
                "output": result.stdout,
                "error": result.stderr if result.stderr else None,
                "command": " ".join(cmd)
            }
        
        elif command == "fetch_availability":
            if not month:
                return {"status": "error", "message": "Month is required for fetching availability"}
            
            # Fetch availability data
            cmd = ["python3", "fetch.py", month]
            result = subprocess.run(
                cmd,
                cwd=os.getcwd(),  # Use current working directory
                capture_output=True,
                text=True,
                timeout=600  # 10 minutes for availability fetching
            )
            return {
                "status": "success" if result.returncode == 0 else "error",
                "output": result.stdout,
                "error": result.stderr if result.stderr else None,
                "command": " ".join(cmd)
            }
        
        elif command == "analyze_results":
            # Analyze results for specific date and location
            date = month  # Reuse month parameter for specific date
            if not date or not location:
                return {"status": "error", "message": "Both date and location are required for analysis"}
            
            # Try to get JSON results first
            json_cmd = ["python3", "format_results_json.py", date, str(distance), location]
            json_result = subprocess.run(
                json_cmd,
                cwd=os.getcwd(),  # Use current working directory
                capture_output=True,
                text=True,
                timeout=60
            )
            
            # Also get text summary
            text_cmd = ["python3", "format_results.py", date, str(distance), location]
            text_result = subprocess.run(
                text_cmd,
                cwd=os.getcwd(),  # Use current working directory
                capture_output=True,
                text=True,
                timeout=60
            )
            
            results = {
                "status": "success",
                "json_output": json_result.stdout if json_result.returncode == 0 else None,
                "json_error": json_result.stderr if json_result.stderr else None,
                "text_output": text_result.stdout if text_result.returncode == 0 else None,
                "text_error": text_result.stderr if text_result.stderr else None,
                "commands": {
                    "json": " ".join(json_cmd),
                    "text": " ".join(text_cmd)
                }
            }
            
            # Try to parse JSON results for structured output
            if results["json_output"]:
                try:
                    parsed_json = json.loads(results["json_output"])
                    results["parsed_results"] = parsed_json
                except json.JSONDecodeError:
                    pass
            
            return results
        
        else:
            return {"status": "error", "message": f"Unknown command: {command}"}
            
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": f"Command timed out: {command}"}
    except Exception as e:
        return {"status": "error", "message": f"Error executing command: {str(e)}"}


def cache_manager_tool(action: str, location: Optional[str] = None, distance: int = 50, month: Optional[str] = None) -> Dict[str, Any]:
    """
    Manage cache operations
    
    Args:
        action: Cache action (check_status, is_fresh, mark_cached)
        location: Location for cache key generation
        distance: Distance for cache key generation  
        month: Month for availability cache
    
    Returns:
        Dict with cache information
    """
    try:
        cache_manager = CacheManager(cache_dir="temp", default_ttl_minutes=30)
        
        if action == "check_status":
            if location:
                campground_fresh = cache_manager.is_campground_list_fresh(location, distance)
                availability_fresh = cache_manager.is_availability_data_fresh(month) if month else None
                
                summary = cache_manager.get_cache_summary_message(location, distance, month)
                
                return {
                    "status": "success",
                    "campground_list_fresh": campground_fresh,
                    "availability_data_fresh": availability_fresh,
                    "summary": summary,
                    "needs_campground_download": not campground_fresh,
                    "needs_availability_download": not availability_fresh if month else False
                }
            else:
                return {"status": "error", "message": "Location required for cache status check"}
        
        elif action == "mark_campground_cached":
            if location:
                cache_manager.mark_campground_list_cached(location, distance)
                return {"status": "success", "message": f"Marked campground list as cached for {location}"}
            else:
                return {"status": "error", "message": "Location required to mark campground cache"}
        
        elif action == "mark_availability_cached":
            if month:
                cache_manager.mark_availability_data_cached(month)
                return {"status": "success", "message": f"Marked availability data as cached for {month}"}
            else:
                return {"status": "error", "message": "Month required to mark availability cache"}
        
        else:
            return {"status": "error", "message": f"Unknown cache action: {action}"}
            
    except Exception as e:
        return {"status": "error", "message": f"Cache error: {str(e)}"}


def extract_query_parameters(query: str, location: Optional[str] = None, date: Optional[str] = None, distance: Optional[int] = None) -> Dict[str, Any]:
    """
    Extract location, date, and distance from a natural language query.
    Instead of using regex, this tool should be used by the LLM to provide structured parameters.
    
    Args:
        query: The original user query (for reference)
        location: The location extracted by the LLM (e.g., "South Lake Tahoe", "Mammoth Lakes, CA")
        date: The date extracted by the LLM (e.g., "2025-07-04", "July 4th weekend", "July 2025")
        distance: The distance in miles (optional, defaults to 50)
    
    Returns:
        Dict with structured parameters for the recreation API
    """
    result = {
        "status": "success",
        "query": query,
        "parameters": {}
    }
    
    # Process location
    if location:
        result["parameters"]["location"] = location
    else:
        result["status"] = "error"
        result["message"] = "No location provided"
        return result
    
    # Process date - standardize to YYYY-MM format for the API
    if date:
        import re
        from datetime import datetime
        
        # Try to parse common date formats
        result["parameters"]["date_original"] = date
        
        # Check for specific date patterns
        if re.match(r"\d{4}-\d{2}-\d{2}", date):
            # Already in YYYY-MM-DD format
            result["parameters"]["month"] = date[:7]  # Extract YYYY-MM
        elif re.match(r"\d{4}-\d{2}", date):
            # Already in YYYY-MM format
            result["parameters"]["month"] = date
        elif "august" in date.lower() or "aug" in date.lower():
            # August 2025 - but check if data is available
            result["parameters"]["month"] = "2025-08"
            result["parameters"]["month_note"] = "August requested - will check if data is available"
        elif "july" in date.lower() or "jul" in date.lower():
            # July 2025
            result["parameters"]["month"] = "2025-07"
        elif "september" in date.lower() or "sep" in date.lower():
            # September 2025
            result["parameters"]["month"] = "2025-09"
        elif "june" in date.lower() or "jun" in date.lower():
            # June 2025
            result["parameters"]["month"] = "2025-06"
        else:
            # Default to July 2025 if we can't parse
            result["parameters"]["month"] = "2025-07"
            result["parameters"]["month_note"] = f"Could not parse '{date}', defaulting to July 2025"
    else:
        # Default to July 2025 if no date specified
        result["parameters"]["month"] = "2025-07"
        result["parameters"]["date_defaulted"] = True
    
    # Process distance
    if distance and isinstance(distance, int) and distance > 0:
        result["parameters"]["distance"] = distance
    else:
        result["parameters"]["distance"] = 50
        result["parameters"]["distance_defaulted"] = True
    
    return result


# Create the ADK agent
def create_adk_campground_agent(lite_mode: bool = False):
    """Create the ADK-based campground agent"""
    
    # Create tools
    recreation_tool = FunctionTool(recreation_api_tool)
    cache_tool = FunctionTool(cache_manager_tool)
    query_parser_tool = FunctionTool(extract_query_parameters)
    
    # Select model based on lite mode
    model_name = "gemini-2.5-flash-lite-preview-06-17" if lite_mode else "gemini-2.5-flash"
    
    # Create the main agent
    agent = Agent(
        name="adk_campground_agent",
        model=model_name,
        description="A campground availability agent that helps users find campsites using Recreation.gov data",
        instruction="""You are a helpful campground availability agent who ALWAYS successfully finds campgrounds when they are available. You help users find available campsites by:

1. **Understanding the user's request**: Use your natural language understanding to extract location, dates, and distance from queries
2. **Checking cache status**: Determine what data needs to be downloaded vs what's already cached
3. **Gathering data efficiently**: Only download what's needed, respecting cache TTL 
4. **Analyzing results**: Find campgrounds with availability for specific dates
5. **Providing helpful responses**: Format results in a clear, user-friendly way

MANDATORY WORKFLOW PROCESS (follow ALL steps):
1. First, analyze the user's query to understand:
   - What location they're asking about (be specific - e.g., "South Lake Tahoe" not just "South Lake")
   - What dates they want (specific dates, weekends, or months)
   - How far they're willing to travel (if mentioned)
   
2. Use extract_query_parameters tool to structure your understanding:
   - Pass the COMPLETE location name you extracted (e.g., "South Lake Tahoe", "Mammoth Lakes, CA")
   - Pass the date/month information you understood
   - Pass the distance if mentioned, otherwise it will default to 50 miles

3. Use cache_manager_tool with the extracted parameters to check what data is already cached

4. Use recreation_api_tool to build campground lists and fetch availability data only when needed

5. **MANDATORY FINAL STEP - ANALYZE RESULTS AND PRESENT FINDINGS**: Use recreation_api_tool with command='analyze_results':
   - You MUST call this even if previous steps suggest no data
   - Pass the same location, distance, and month parameters
   - **PARSING THE RESPONSE**: The tool returns a response object with these fields:
     * status: 'success' or 'error'
     * json_output: Contains stringified JSON with campground data
     * text_output: Contains human-readable summary
   - **DETERMINING AVAILABILITY**: Look inside json_output for the 'total_found' number:
     * If total_found > 0: CAMPGROUNDS ARE AVAILABLE - present them to the user
     * If total_found = 0: No campgrounds found
   - **PRESENTING RESULTS**: When total_found > 0, extract and show:
     * Number of campgrounds found
     * Names and distances of campgrounds
     * Available dates for Wednesday (dates ending in Wednesday or containing "08-06", "08-13", "08-20", "08-27")
   - **EXAMPLE SUCCESS SCENARIO**: 
     * Tool returns: {"total_found": 6, "campgrounds": [{"name": "Badgers Den", "distance": 3.0, "available_sites": [...]}]}
     * You should respond: "Great! I found 6 campgrounds with availability! Here are your options for Wednesdays in August..."
   - **NEVER say "no availability" if total_found > 0**

IMPORTANT GUIDELINES:
- When extracting locations, use your knowledge to get the FULL location name (e.g., "South Lake Tahoe" not "South Lake")
- Be conversational and explain what you're doing at each step
- Always check cache status before downloading to avoid unnecessary API calls
- If data is cached and fresh, skip the download step
- For dates like "July 4th weekend", understand this means around July 4, 2025

**CRITICAL RESPONSE INTERPRETATION RULES**:
1. **ALWAYS check json_output first** - it contains the actual campground data
2. **Look for "total_found": NUMBER** in the json_output string
3. **If total_found > 0**: 
   - SUCCESS! Campgrounds are available
   - Extract campground names, distances, and available dates
   - Show Wednesday dates (2025-08-06, 2025-08-13, 2025-08-20, 2025-08-27)
   - Present results to user with enthusiasm
4. **If total_found = 0**: Only then say no availability
5. **EXAMPLE SUCCESSFUL RESPONSE**: 
   - json_output: '{"total_found": 6, "campgrounds": [...]}'
   - This means 6 campgrounds ARE available - show them!
6. **NEVER conclude "no availability" without checking total_found first**

- **HANDLE MISSING DATA GRACEFULLY**: If the requested month (e.g., August) has no availability data, check what months ARE available and suggest alternatives
- **PROVIDE HELPFUL ALTERNATIVES**: If August data isn't available but July is, suggest July dates or explain what data is available
- Always end with a summary of found campgrounds and availability, or helpful suggestions if no data is available

Be helpful, efficient, and informative in your responses!

**FINAL CRITICAL RULE**: After calling analyze_results, if the tool response contains json_output with total_found > 0, you MUST present the campground results as successful findings. Do NOT say "no availability" - instead say something like "Great news! I found [X] campgrounds with availability for you!" and list them.""",
        tools=[recreation_tool, cache_tool, query_parser_tool]
    )
    
    return agent


class ADKCampgroundAgent:
    """Wrapper class for the ADK campground agent to match the interface"""
    
    def __init__(self, project_dir: str = None, update_callback=None, cache_ttl_minutes: int = 30, lite_mode: bool = False):
        """Initialize the ADK agent wrapper"""
        self.project_dir = Path(project_dir) if project_dir else Path.cwd()
        self.update_callback = update_callback or print
        self.cache_ttl_minutes = cache_ttl_minutes
        self.lite_mode = lite_mode
        
        # Create ADK agent with appropriate model
        self.agent = create_adk_campground_agent(lite_mode=lite_mode)
        
        # Don't pre-create runner or session - create fresh for each request
    
    async def send_update(self, message: str, type: str = "info"):
        """Send an update to the callback"""
        if self.update_callback:
            if asyncio.iscoroutinefunction(self.update_callback):
                await self.update_callback({"type": type, "message": message})
            else:
                self.update_callback({"type": type, "message": message})
    
    async def send_campground_results(self, campground_data):
        """Send campground results in the format expected by the frontend"""
        if self.update_callback:
            # Structure the data as expected by the frontend
            result_data = {
                "type": "campground_results",
                "campgrounds": campground_data.get("campgrounds", []),
                "date": campground_data.get("date"),
                "location": campground_data.get("location"),
                "max_distance": campground_data.get("max_distance"),
                "total_found": campground_data.get("total_found", 0)
            }
            
            if asyncio.iscoroutinefunction(self.update_callback):
                await self.update_callback(result_data)
            else:
                self.update_callback(result_data)
    
    async def process_natural_language_query(self, user_query: str):
        """Process a natural language query using ADK"""
        
        await self.send_update(f"🧠 ADK Agent analyzing: {user_query!r}")
        
        try:
            await self.send_update("🤖 Starting ADK agent processing...")
            
            # Create runner - it has built-in session service
            from google.adk.runners import InMemoryRunner
            
            # Create runner with app name
            runner = InMemoryRunner(agent=self.agent, app_name="campground_agent")
            
            # Generate unique user/session IDs
            import uuid
            session_id = str(uuid.uuid4())
            user_id = f"web_user_{uuid.uuid4().hex[:8]}"
            
            await self.send_update(f"📝 Session: {session_id[:8]}... User: {user_id}")
            
            # Create session using the runner's built-in session service
            session = await runner.session_service.create_session(
                app_name="campground_agent",
                user_id=user_id,
                session_id=session_id
            )
            
            await self.send_update(f"✅ Session created successfully")
            
            # Create user message
            user_content = types.UserContent(parts=[types.Part(text=user_query)])
            
            await self.send_update("🚀 Sending query to ADK agent...")
            
            # Run the agent with properly initialized session
            async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=user_content
            ):
                await self.send_update(f"📨 Processing ADK response...", "status")
                
                # Handle different event types
                if hasattr(event, 'content') and hasattr(event.content, 'parts'):
                    for part in event.content.parts:
                        if hasattr(part, 'text') and part.text:
                            await self.send_update(part.text, "agent_response")
                        elif hasattr(part, 'function_call') and part.function_call:
                            tool_name = getattr(part.function_call, 'name', 'unknown_tool')
                            await self.send_update(f"🔧 Using tool: {tool_name}", "tool_use")
                        elif hasattr(part, 'function_response'):
                            response_preview = str(part.function_response.response)[:200]
                            if len(str(part.function_response.response)) > 200:
                                response_preview += "..."
                            await self.send_update(f"✅ Tool result: {response_preview}", "tool_result")
                            
                            # Check for campground results to format nicely
                            if hasattr(part.function_response, 'response'):
                                response = part.function_response.response
                                if isinstance(response, dict) and 'parsed_results' in response:
                                    await self.send_update(json.dumps(response['parsed_results']), "campground_results")
                                elif isinstance(response, dict) and 'json_output' in response and response.get('json_output'):
                                    # Handle analyze_results responses directly
                                    try:
                                        parsed_json = json.loads(response['json_output'])
                                        if parsed_json.get('total_found', 0) > 0:
                                            # Send structured data for the frontend
                                            await self.send_campground_results(parsed_json)
                                    except json.JSONDecodeError:
                                        pass
                elif hasattr(event, 'message'):
                    # Handle message events
                    await self.send_update(f"💬 Agent: {event.message}", "agent_response")
                else:
                    # Log unknown event types for debugging
                    await self.send_update(f"🔍 Event type: {type(event).__name__}", "status")
                
        except Exception as e:
            await self.send_update(f"❌ ADK Agent error: {str(e)}", "error")
            import traceback
            error_details = traceback.format_exc()
            await self.send_update(f"🐛 Error details: {error_details}", "error")
            raise


# For backward compatibility
def create_adk_agent_for_web():
    """Create an ADK agent configured for web use"""
    
    def web_update_callback(update_data):
        """Callback that formats updates for web streaming"""
        print(f"ADK Update: {update_data}")
    
    return ADKCampgroundAgent(
        project_dir=os.getcwd(),  # Use current working directory
        update_callback=web_update_callback
    )