"""
Web Tools - Ported from OpenClaw agents/tools/web-fetch.ts and web-search.ts

Provides web fetching and searching capabilities.
"""

import os
import json
import asyncio
import urllib.parse
from typing import Dict, List, Optional, Any
import logging

logger = logging.getLogger("cursor_enhanced.openclaw_web")

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

class WebFetchTool:
    """Web fetch tool (ported from web-fetch.ts)"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.fetch_config = self.config.get("tools", {}).get("web", {}).get("fetch", {})
        self.enabled = self.fetch_config.get("enabled", True)
        self.max_chars = self.fetch_config.get("maxChars", 50_000)
        self.timeout_seconds = self.fetch_config.get("timeoutSeconds", 30)
        self.user_agent = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_2) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
    
    async def execute(self, url: str, extract_mode: str = "markdown", 
                     max_chars: Optional[int] = None) -> Dict[str, Any]:
        """Fetch and extract content from URL"""
        if not self.enabled:
            return {"error": "Web fetch is disabled"}
        
        if not HTTPX_AVAILABLE:
            return {"error": "httpx library required. Install with: pip install httpx"}
        
        max_chars = max_chars or self.max_chars
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(
                    url,
                    headers={"User-Agent": self.user_agent},
                    follow_redirects=True
                )
                response.raise_for_status()
                
                content = response.text
                
                # Extract readable content (simplified - full version would use readability)
                if extract_mode == "text":
                    # Basic text extraction
                    import re
                    # Remove HTML tags
                    text = re.sub(r'<[^>]+>', '', content)
                    # Clean up whitespace
                    text = re.sub(r'\s+', ' ', text).strip()
                    extracted = text
                else:
                    # Markdown extraction (simplified)
                    extracted = self._html_to_markdown(content)
                
                # Truncate if needed
                if len(extracted) > max_chars:
                    extracted = extracted[:max_chars] + "..."
                
                return {
                    "url": url,
                    "content": extracted,
                    "extractMode": extract_mode,
                    "chars": len(extracted),
                    "status": response.status_code
                }
        except Exception as e:
            logger.error(f"Web fetch failed: {e}")
            return {"error": str(e), "url": url}
    
    def _html_to_markdown(self, html: str) -> str:
        """Convert HTML to markdown (simplified)"""
        # This is a simplified version - full implementation would use a proper HTML parser
        import re
        # Remove script and style tags
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
        # Convert common HTML tags to markdown
        html = re.sub(r'<h1[^>]*>(.*?)</h1>', r'# \1', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<h2[^>]*>(.*?)</h2>', r'## \1', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<h3[^>]*>(.*?)</h3>', r'### \1', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<p[^>]*>(.*?)</p>', r'\1\n\n', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', r'[\2](\1)', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<strong[^>]*>(.*?)</strong>', r'**\1**', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<em[^>]*>(.*?)</em>', r'*\1*', html, flags=re.DOTALL | re.IGNORECASE)
        # Remove remaining HTML tags
        html = re.sub(r'<[^>]+>', '', html)
        # Clean up whitespace
        html = re.sub(r'\n\s*\n\s*\n+', '\n\n', html)
        html = re.sub(r'[ \t]+', ' ', html)
        return html.strip()

class WebSearchTool:
    """Web search tool (ported from web-search.ts)"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.search_config = self.config.get("tools", {}).get("web", {}).get("search", {})
        self.enabled = self.search_config.get("enabled", True)
        self.provider = self.search_config.get("provider", "duckduckgo")
        self.api_key = self.search_config.get("apiKey")
    
    async def execute(self, query: str, max_results: int = 5) -> Dict[str, Any]:
        """Search the web"""
        if not self.enabled:
            return {"error": "Web search is disabled"}
        
        # Simplified implementation - full version would use actual search APIs
        # For now, return a placeholder
        logger.warning("Web search is not fully implemented - requires search API integration")
        return {
            "query": query,
            "results": [],
            "provider": self.provider,
            "note": "Web search requires API integration (DuckDuckGo, Google, etc.)"
        }
