"""
Tool Executor - Parses agent responses and executes tools mentioned
"""

import re
import asyncio
import logging
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger("cursor_enhanced.tool_executor")

async def execute_tool_from_response(
    agent_response: str,
    openclaw_integration,
    max_iterations: int = 3
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Parse agent response for tool usage and execute tools.
    Returns: (updated_response, tool_results)
    """
    if not openclaw_integration:
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
    }
    
    # Extract URLs from response
    url_pattern = r'https?://[^\s\)]+'
    urls = re.findall(url_pattern, agent_response, re.IGNORECASE)
    
    # Extract search queries
    search_queries = []
    for pattern in tool_patterns.get('web_search', []):
        matches = re.finditer(pattern, agent_response, re.IGNORECASE)
        for match in matches:
            if match.groups():
                search_queries.append(match.group(1).strip())
    
    # Execute web_fetch if URLs found
    for url in urls[:3]:  # Limit to 3 URLs
        try:
            logger.info(f"Executing web_fetch for URL: {url}")
            result = await openclaw_integration.tool_registry.execute("web_fetch", url=url)
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
            result = await openclaw_integration.tool_registry.execute("web_search", query=query)
            tool_results.append({"tool": "web_search", "query": query, "result": result})
            
            # Append result to response
            if "error" not in result:
                updated_response += f"\n\n[Tool Result: web_search for '{query}']\n{result.get('note', 'Search completed')}"
            else:
                updated_response += f"\n\n[Tool Error: web_search for '{query}' - {result.get('error', 'Unknown error')}]"
        except Exception as e:
            logger.error(f"Failed to execute web_search for {query}: {e}")
            tool_results.append({"tool": "web_search", "query": query, "error": str(e)})
    
    return updated_response, tool_results
