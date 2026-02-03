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
            result = await openclaw_integration.tool_registry.execute("web_fetch", "", {"url": url})
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
            result = await openclaw_integration.tool_registry.execute("web_search", "", {"query": query})
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
    
    return updated_response, tool_results
