#!/usr/bin/env python3
"""
Simple web frontend for the Campground Availability Agent with real-time streaming
"""

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import anyio
import json
import asyncio
from pathlib import Path
import queue
import threading
import os

# Import Claude Code agent if available
try:
    from streaming_agent import StreamingCampgroundAgent
    from claude_code_sdk import AssistantMessage, TextBlock, ToolUseBlock, ToolResultBlock
    CLAUDE_CODE_AVAILABLE = True
except ImportError as e:
    print(f"⚠️ Claude Code SDK not available: {e}")
    CLAUDE_CODE_AVAILABLE = False

# Import ADK agent if available
try:
    from adk_agent import ADKCampgroundAgent
    ADK_AVAILABLE = True
except ImportError as e:
    print(f"⚠️ ADK not available: {e}")
    ADK_AVAILABLE = False

app = FastAPI(title="Campground Availability Agent", description="Find campgrounds with natural language queries")

# Setup templates directory
templates_dir = Path("templates")
templates_dir.mkdir(exist_ok=True)
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Serve the main page"""
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/query")
async def process_query(query: str = Form(...), agent: str = Form("claude-code"), lite_mode: bool = Form(False)):
    """Process a natural language campground query with real-time streaming"""
    
    async def generate_response():
        """Stream the agent's response with real-time updates"""
        
        # Create a queue for updates
        update_queue = asyncio.Queue()
        
        async def update_callback(update_data):
            """Callback to receive updates from the agent"""
            await update_queue.put(update_data)
        
        # Show the user's query
        yield f"data: {json.dumps({'type': 'status', 'message': f'🔍 Processing query: {query!r}'})}" + "\n\n"
        
        try:
            # Choose agent based on selection
            if agent == "google-adk" and ADK_AVAILABLE:
                model_name = "Gemini 2.5 Flash Lite" if lite_mode else "Gemini 2.5 Flash"
                yield f"data: {json.dumps({'type': 'status', 'message': f'🤖 Using Google ADK with {model_name}'})}" + "\n\n"
                # Create ADK agent
                campground_agent = ADKCampgroundAgent(
                    project_dir=str(Path.cwd()),
                    update_callback=update_callback,
                    cache_ttl_minutes=30,
                    lite_mode=lite_mode
                )
            else:
                if agent == "google-adk" and not ADK_AVAILABLE:
                    yield f"data: {json.dumps({'type': 'status', 'message': '⚠️ ADK not available, checking Claude Code SDK...'})}" + "\n\n"
                
                if CLAUDE_CODE_AVAILABLE:
                    yield f"data: {json.dumps({'type': 'status', 'message': '🤖 Using Claude Code SDK'})}" + "\n\n"
                    # Create Claude Code agent (default)
                    campground_agent = StreamingCampgroundAgent(
                        project_dir=str(Path.cwd()),
                        update_callback=update_callback,
                        cache_ttl_minutes=30  # Cache TTL in minutes - configurable
                    )
                else:
                    yield f"data: {json.dumps({'type': 'error', 'message': '❌ Neither ADK nor Claude Code SDK available'})}" + "\n\n"
                    return
            
            # Start the agent task with raw query
            agent_task = asyncio.create_task(
                campground_agent.process_natural_language_query(query)
            )
            
            # Stream updates as they come in
            while not agent_task.done():
                try:
                    # Wait for update with timeout
                    update = await asyncio.wait_for(update_queue.get(), timeout=0.1)
                    yield f"data: {json.dumps(update)}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield f"data: {json.dumps({'type': 'keepalive'})}\n\n"
                    continue
            
            # Get any remaining updates
            while not update_queue.empty():
                update = await update_queue.get()
                yield f"data: {json.dumps(update)}\n\n"
            
            # Check if agent completed successfully
            try:
                await agent_task
                yield f"data: {json.dumps({'type': 'success', 'message': 'Search completed successfully!'})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': f'Agent error: {str(e)}'})}\n\n"
            
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': f'Error: {str(e)}'})}\n\n"
        
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
    
    return StreamingResponse(
        generate_response(),
        media_type="text/plain",
        headers={
            "Cache-Control": "no-cache", 
            "Connection": "keep-alive",
            "Content-Type": "text/plain; charset=utf-8"
        }
    )

@app.post("/query-detailed")
async def process_detailed_query(
    location: str = Form(...),
    date: str = Form(...),
    distance: int = Form(50),
    route: str = Form(None)
):
    """Process a detailed campground query with specific parameters"""
    
    async def generate_response():
        """Stream the agent's response"""
        try:
            yield f"data: {json.dumps({'type': 'status', 'message': f'Searching campgrounds near {location} for {date}'})}\n\n"
            
            if route and route.strip():
                await agent.find_availability(location, date, distance, route.strip())
            else:
                await agent.find_availability(location, date, distance)
            
            yield f"data: {json.dumps({'type': 'result', 'message': 'Search completed!'})}\n\n"
            
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': f'Error: {str(e)}'})}\n\n"
        
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
    
    return StreamingResponse(
        generate_response(),
        media_type="text/plain",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
    )

if __name__ == "__main__":
    import uvicorn
    import os
    
    port = int(os.environ.get("PORT", 8000))
    print("🏕️ Starting Campground Availability Agent Web Interface")
    print(f"📍 Server will run on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)