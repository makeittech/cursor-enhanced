"""
Tool Executor - Parses agent responses and executes tools mentioned
"""

import re
import json
import asyncio
import logging
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger("cursor_enhanced.tool_executor")

async def execute_tool_from_response(
    agent_response: str,
    runtime_integration,
    max_iterations: int = 3,
    *,
    last_user_message: Optional[str] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Parse agent response for tool usage and execute tools.
    If last_user_message is set and a delegate is run, the task is augmented with
    a minimal "User asked: <truncated>" line so the sub-agent has the exact request
    without needing to search, with minimal tokens.
    Returns: (updated_response, tool_results)
    """
    if not runtime_integration:
        return agent_response, []
    
    tool_results = []
    updated_response = agent_response
    
    # Pattern to detect tool usage mentions
    # Look for phrases like "fetch", "search", "web_fetch", "web_search", etc.
    tool_patterns = {
        'web_fetch': [
            r'fetch(?:ing)?\s+(?:the\s+)?(?:web(?:page|site)?|page|url|link)\s+(?:at|from)?\s*(?:https?://[^\s\)]+)',
            r'fetch(?:ing)?\s+(?:https?://[^\s\)]+)',
            r'get(?:ting)?\s+(?:the\s+)?(?:content|page|webpage)\s+(?:from|at)?\s*(?:https?://[^\s\)]+)',
        ],
        'web_search': [
            r'search(?:ing)?\s+(?:the\s+)?web\s+(?:for)?\s+["\']?([^"\']+)["\']?',
            r'search(?:ing)?\s+for\s+["\']?([^"\']+)["\']?',
            r'look(?:ing)?\s+up\s+["\']?([^"\']+)["\']?',
        ],
        'memory_search': [
            r'search(?:ing)?\s+(?:the\s+)?memory\s+(?:for)?\s+["\']?([^"\']+)["\']?',
            r'look(?:ing)?\s+(?:in|through)\s+memory\s+(?:for)?\s+["\']?([^"\']+)["\']?',
        ],
        'delegate': [
            r'delegate\s+(?:to|task to)\s+(?:the\s+)?(researcher|coder|reviewer|writer|home_assistant|ha)\s*[:\-]\s*([^\n]+?)(?=\n\n|\n\[|$)',
            r'ask\s+(?:the\s+)?(researcher|coder|reviewer|writer|home_assistant|ha)\s+(?:agent\s+)?to\s+([^\n]+?)(?=\n\n|\n\[|$)',
            r'have\s+(?:the\s+)?(researcher|coder|reviewer|writer|home_assistant|ha)\s+([^\n]+?)(?=\n\n|\n\[|$)',
        ],
        'weather': [
            r'(?:get|fetch|check)(?:ting)?\s+(?:the\s+)?(?:current\s+)?weather\s+(?:in|for|at)\s+([^\.\n,]+)',
            r'(?:get|fetch|check)(?:ting)?\s+(?:the\s+)?forecast\s+(?:in|for|at)\s+([^\.\n,]+)',
            r'weather\s+(?:in|for|at)\s+([^\.\n,]+)',
        ],
        'smart_delegate': [
            # Explicit: "smart delegate: <task>" or "delegate to stronger model: <task>"
            r'smart\s+delegat(?:e|ing)\s*[:\-]\s*(.+?)(?=\n\n|\n\[|$)',
            r'delegat(?:e|ing)\s+(?:this\s+)?(?:to\s+(?:a\s+)?)?(?:stronger|better|more\s+capable|optimal|profound)\s+(?:model|agent)\s*[:\-]\s*(.+?)(?=\n\n|\n\[|$)',
            r'(?:need|use|pick|choose)\s+(?:a\s+)?(?:stronger|better|more\s+capable|optimal)\s+model\s+(?:for|to)\s*[:\-]?\s*(.+?)(?=\n\n|\n\[|$)',
        ],
        'cursor_agent': [
            # "cursor agent launch: <prompt>" or "launch cursor agent: <prompt>"
            r'cursor\s+agent\s+(launch|create|status|list|conversation|followup|follow[_-]?up|stop|delete|models|repos|repositories|me|info)\s*[:\-]\s*(.+?)(?=\n\n|\n\[|$)',
            r'(launch|create)\s+(?:a\s+)?cursor\s+(?:cloud\s+)?agent\s*[:\-]\s*(.+?)(?=\n\n|\n\[|$)',
            r'(?:get|check)\s+cursor\s+agent\s+(status|conversation)\s+(?:for\s+)?(\S+)',
            r'(list)\s+cursor\s+(?:cloud\s+)?agents?()',
            r'(delete|stop)\s+cursor\s+agent\s+(\S+)',
            r'cursor\s+agent\s+(followup|follow[_-]?up)\s+(\S+)\s*[:\-]\s*(.+?)(?=\n\n|\n\[|$)',
        ],
    }
    
    # Extract URLs from response
    url_pattern = r'https?://[^\s\)]+'
    urls = re.findall(url_pattern, agent_response, re.IGNORECASE)
    
    def clean_query(query: str) -> str:
        """Clean extracted query by removing quotes, parentheses, and trailing punctuation"""
        if not query:
            return ""
        # Strip whitespace
        query = query.strip()
        # Remove surrounding quotes (single or double)
        query = query.strip('"\'')
        # Remove trailing punctuation and parentheses
        query = query.rstrip('.,;:!?)')
        # Remove leading/trailing whitespace again
        query = query.strip()
        return query
    
    # Extract search queries - improved patterns
    search_queries = []
    for pattern in tool_patterns.get('web_search', []):
        matches = re.finditer(pattern, agent_response, re.IGNORECASE)
        for match in matches:
            if match.groups():
                query = clean_query(match.group(1))
                if query and len(query) > 2:  # Valid query
                    search_queries.append(query)
    
    # Extract memory search queries
    memory_queries = []
    for pattern in tool_patterns.get('memory_search', []):
        matches = re.finditer(pattern, agent_response, re.IGNORECASE)
        for match in matches:
            if match.groups():
                query = clean_query(match.group(1))
                if query and len(query) > 2:
                    memory_queries.append(query)

    # Also look for phrases like "funny cat videos", "search for X", etc.
    # Pattern: "search(ing)? (the web )?for [phrase]"
    additional_patterns = [
        r'search(?:ing)?\s+(?:the\s+web\s+)?for\s+([^\.\n]+?)(?:\.|$|\n)',
        r'looking\s+up\s+([^\.\n]+?)(?:\.|$|\n)',
        r'find(?:ing)?\s+([^\.\n]+?)(?:\.|$|\n)',
    ]
    for pattern in additional_patterns:
        matches = re.finditer(pattern, agent_response, re.IGNORECASE)
        for match in matches:
            if match.groups():
                query = clean_query(match.group(1))
                # Clean up common prefixes/suffixes
                query = re.sub(r'^(for|about|on)\s+', '', query, flags=re.IGNORECASE)
                query = query.strip('.,;:!?)')
                if query and len(query) > 2 and query not in search_queries:
                    search_queries.append(query)
    
    # Execute web_fetch if URLs found
    for url in urls[:3]:  # Limit to 3 URLs
        try:
            logger.info(f"Executing web_fetch for URL: {url}")
            # ToolRegistry.execute expects (tool_name, action, params)
            # For web_fetch, action is empty string, params is a dict
            result = await runtime_integration.tool_registry.execute("web_fetch", "", {"url": url})
            tool_results.append({"tool": "web_fetch", "url": url, "result": result})
            
            # Append result to response
            if "error" not in result:
                content_preview = result.get("content", "")[:500]
                updated_response += f"\n\n[Tool Result: web_fetch for {url}]\n{content_preview}..."
            else:
                updated_response += f"\n\n[Tool Error: web_fetch for {url} - {result.get('error', 'Unknown error')}]"
        except Exception as e:
            logger.error(f"Failed to execute web_fetch for {url}: {e}")
            tool_results.append({"tool": "web_fetch", "url": url, "error": str(e)})
    
    # Execute web_search if queries found
    for query in search_queries[:2]:  # Limit to 2 queries
        try:
            logger.info(f"Executing web_search for query: {query}")
            # ToolRegistry.execute expects (tool_name, action, params)
            # For web_search, action is empty string, params is a dict
            result = await runtime_integration.tool_registry.execute("web_search", "", {"query": query})
            tool_results.append({"tool": "web_search", "query": query, "result": result})
            
            # Append result to response
            if result and "error" not in result:
                # Web search is a placeholder, so provide helpful message
                note = result.get('note', 'Web search completed')
                updated_response += f"\n\n[Tool Result: web_search for '{query}']\n{note}\nNote: Web search requires API integration. For now, you can suggest the user search manually or use web_fetch with specific URLs."
            elif result:
                error_msg = result.get('error', 'Unknown error')
                updated_response += f"\n\n[Tool Error: web_search for '{query}']\n{error_msg}"
            else:
                updated_response += f"\n\n[Tool Note: web_search for '{query}']\nWeb search tool executed but returned no results. This is expected as web search requires API integration."
        except Exception as e:
            logger.error(f"Failed to execute web_search for {query}: {e}", exc_info=True)
            tool_results.append({"tool": "web_search", "query": query, "error": str(e)})
            updated_response += f"\n\n[Tool Error: web_search for '{query}']\nError: {str(e)}"

    # Execute memory_search if queries found
    for query in memory_queries[:2]:
        try:
            logger.info(f"Executing memory_search for query: {query}")
            result = await runtime_integration.tool_registry.execute("memory_search", "", {"query": query})
            tool_results.append({"tool": "memory_search", "query": query, "result": result})
            if result and "error" not in result:
                entries = result.get("results") or []
                if entries:
                    lines = []
                    for entry in entries[:3]:
                        path = entry.get("path", "unknown")
                        start = entry.get("startLine")
                        end = entry.get("endLine")
                        line_range = ""
                        if isinstance(start, int) and isinstance(end, int):
                            line_range = f"#L{start}-L{end}"
                        snippet = entry.get("snippet") or entry.get("text", "")
                        lines.append(f"- {path}{line_range}: {snippet}")
                    updated_response += (
                        f"\n\n[Tool Result: memory_search for '{query}']\n" +
                        "\n".join(lines)
                    )
                else:
                    updated_response += f"\n\n[Tool Result: memory_search for '{query}']\nNo results found."
            else:
                error_msg = result.get("error", "Unknown error") if result else "Unknown error"
                updated_response += f"\n\n[Tool Error: memory_search for '{query}']\n{error_msg}"
        except Exception as e:
            logger.error(f"Failed to execute memory_search for {query}: {e}", exc_info=True)
            tool_results.append({"tool": "memory_search", "query": query, "error": str(e)})
            updated_response += f"\n\n[Tool Error: memory_search for '{query}']\nError: {str(e)}"

    # Delegate: extract "delegate to <persona>: <task>" or "ask the <persona> to <task>"
    delegate_matches = []
    for pattern in tool_patterns.get("delegate", []):
        for match in re.finditer(pattern, agent_response, re.IGNORECASE | re.DOTALL):
            if match.lastindex and match.lastindex >= 2:
                persona_id = (match.group(1) or "").strip().lower()
                task = (match.group(2) or "").strip()
                if persona_id and task and len(task) > 5:
                    if persona_id == "ha":
                        persona_id = "home_assistant"
                    delegate_matches.append((persona_id, task))
    # Max chars of last user message to append so sub-agent has exact request (minimal tokens, no misinterpretation)
    DELEGATE_USER_CTX_MAX = 350

    for persona_id, task in delegate_matches[:1]:  # Limit 1 delegate per response to avoid long runs
        task_to_send = task
        if last_user_message and (s := last_user_message.strip()):
            one_line = s.split("\n")[0][:DELEGATE_USER_CTX_MAX].strip()
            if one_line:
                task_to_send = f"{task.strip()}\nUser asked: {one_line}"
        try:
            logger.info(f"Executing delegate tool: persona={persona_id}, task={task_to_send[:80]}...")
            result = await runtime_integration.tool_registry.execute(
                "delegate", "", {"persona_id": persona_id, "task": task_to_send}
            )
            tool_results.append({"tool": "delegate", "persona_id": persona_id, "result": result})
            if result.get("success"):
                resp = result.get("response", "") or ""
                updated_response += f"\n\n[Delegate Result: {persona_id}]\n{resp[:4000]}{'...' if len(resp) > 4000 else ''}"
            else:
                updated_response += f"\n\n[Delegate Error: {persona_id}] {result.get('error', 'Unknown error')}"
        except Exception as e:
            logger.error(f"Failed to run delegate for {persona_id}: {e}")
            tool_results.append({"tool": "delegate", "persona_id": persona_id, "error": str(e)})
            updated_response += f"\n\n[Delegate Error: {persona_id}] {str(e)}"

    # Execute weather tool if city mentions found
    weather_cities = []
    for pattern in tool_patterns.get('weather', []):
        for match in re.finditer(pattern, agent_response, re.IGNORECASE):
            if match.groups():
                city = clean_query(match.group(1))
                if city and len(city) > 1 and city not in weather_cities:
                    weather_cities.append(city)

    for city in weather_cities[:1]:  # Limit to 1 weather lookup per response
        try:
            logger.info(f"Executing weather tool for city: {city}")
            result = await runtime_integration.tool_registry.execute("weather", "", {"city": city})
            tool_results.append({"tool": "weather", "city": city, "result": result})
            if result and "error" not in result:
                cur = result.get("current", {})
                forecast = result.get("forecast", [])
                city_name = result.get("city", city)
                lines = [f"\n\n[Weather: {city_name}]"]
                if cur:
                    lines.append(
                        f"Now: {cur.get('weather', '?')}, {cur.get('temperature_c', '?')}°C "
                        f"(feels {cur.get('feels_like_c', '?')}°C), "
                        f"humidity {cur.get('humidity_pct', '?')}%, "
                        f"wind {cur.get('wind_speed_kmh', '?')} km/h"
                    )
                if forecast:
                    lines.append("Forecast:")
                    for day in forecast[:7]:
                        lines.append(
                            f"  {day.get('date', '?')}: {day.get('weather', '?')}, "
                            f"{day.get('temp_min_c', '?')}–{day.get('temp_max_c', '?')}°C, "
                            f"precip {day.get('precipitation_mm', 0)}mm"
                        )
                updated_response += "\n".join(lines)
            else:
                updated_response += f"\n\n[Weather Error: {result.get('error', 'Unknown error')}]"
        except Exception as e:
            logger.error(f"Failed to execute weather tool for {city}: {e}")
            tool_results.append({"tool": "weather", "city": city, "error": str(e)})
            updated_response += f"\n\n[Weather Error: {str(e)}]"

    # Smart delegate: detect "smart delegate: <task>" or "delegate to stronger model: <task>"
    smart_delegate_matches = []
    for pattern in tool_patterns.get("smart_delegate", []):
        for match in re.finditer(pattern, agent_response, re.IGNORECASE | re.DOTALL):
            if match.groups():
                task = (match.group(1) or "").strip()
                if task and len(task) > 10:
                    smart_delegate_matches.append(task)

    for task in smart_delegate_matches[:1]:  # Limit 1 per response
        # Augment with original user request for context
        task_to_send = task
        if last_user_message and (s := last_user_message.strip()):
            one_line = s.split("\n")[0][:500].strip()
            if one_line:
                task_to_send = f"{task.strip()}\n\nOriginal user request: {one_line}"
        try:
            logger.info(f"Executing smart_delegate: task={task_to_send[:80]}...")
            result = await runtime_integration.tool_registry.execute(
                "smart_delegate", "", {"task": task_to_send}
            )
            tool_results.append({"tool": "smart_delegate", "result": result})
            announcement = result.get("announcement", "")
            if announcement:
                updated_response += f"\n\n{announcement}"
            if result.get("success"):
                resp = result.get("response", "") or ""
                updated_response += f"\n\n[Smart Delegate Response]\n{resp[:6000]}{'...' if len(resp) > 6000 else ''}"
            else:
                updated_response += f"\n\n[Smart Delegate Error] {result.get('error', 'Unknown error')}"
        except Exception as e:
            logger.error(f"Failed to run smart_delegate: {e}")
            tool_results.append({"tool": "smart_delegate", "error": str(e)})
            updated_response += f"\n\n[Smart Delegate Error] {str(e)}"

    # Cursor Agent: detect "cursor agent <action>: <params>" patterns
    cursor_agent_matches = []
    for pattern in tool_patterns.get("cursor_agent", []):
        for match in re.finditer(pattern, agent_response, re.IGNORECASE | re.DOTALL):
            groups = [g for g in match.groups() if g is not None]
            if len(groups) >= 1:
                action = groups[0].strip().lower()
                rest = groups[1].strip() if len(groups) >= 2 else ""
                extra = groups[2].strip() if len(groups) >= 3 else ""
                cursor_agent_matches.append((action, rest, extra))

    for action, rest, extra in cursor_agent_matches[:1]:
        try:
            params: Dict[str, Any] = {"action": action}
            # Parse params based on action
            if action in ("launch", "create"):
                params["prompt"] = rest
                # MODEL POLICY: Never pass a model from AI-generated text.
                # The AI must NOT silently choose a model — always use "default"
                # (Cursor auto-selects). user_confirmed_model stays False so that
                # even if "model" leaks in, CursorAgentTool.launch() will reject it.
            elif action in ("status", "get", "conversation", "stop", "delete"):
                params["agent_id"] = rest
            elif action in ("followup", "follow_up", "follow-up"):
                params["agent_id"] = rest
                if extra:
                    params["prompt"] = extra
            elif action in ("list",):
                pass  # no extra params needed
            # else: models, repos, me — no extra params

            logger.info("Executing cursor_agent: action=%s params=%s", action, {k: v[:60] if isinstance(v, str) else v for k, v in params.items()})
            result = await runtime_integration.tool_registry.execute("cursor_agent", "", params)
            tool_results.append({"tool": "cursor_agent", "action": action, "result": result})
            summary = result.get("_summary", "")
            if "error" in result:
                updated_response += f"\n\n[Cursor Agent Error] {result['error']}"
            elif summary:
                updated_response += f"\n\n[Cursor Agent: {action}]\n{summary}"
            else:
                # Fallback: show JSON
                safe = {k: v for k, v in result.items() if not k.startswith("_")}
                updated_response += f"\n\n[Cursor Agent: {action}]\n{json.dumps(safe, indent=2)[:3000]}"
        except Exception as e:
            logger.error("Failed to execute cursor_agent (%s): %s", action, e)
            tool_results.append({"tool": "cursor_agent", "action": action, "error": str(e)})
            updated_response += f"\n\n[Cursor Agent Error: {action}] {str(e)}"

    return updated_response, tool_results
