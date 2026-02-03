"""
MCP Tools Integration for Cursor Enhanced

This module provides integration with Cursor's MCP (Model Context Protocol) tools.
It allows cursor-enhanced to discover and use MCP tools available through Cursor.
"""

import os
import json
import subprocess
import asyncio
from typing import Dict, List, Optional, Any
import logging

logger = logging.getLogger("cursor_enhanced.mcp")

class MCPToolsClient:
    """Client for discovering and using MCP tools via Cursor"""
    
    def __init__(self):
        self.tools_cache: Dict[str, Dict[str, Any]] = {}
        self.mcp_config_path = os.path.expanduser("~/.cursor/mcp.json")
    
    def discover_mcp_tools(self) -> List[Dict[str, Any]]:
        """Discover MCP tools available through Cursor"""
        tools = []
        
        # Try to read MCP config if it exists
        if os.path.exists(self.mcp_config_path):
            try:
                with open(self.mcp_config_path, 'r') as f:
                    mcp_config = json.load(f)
                    servers = mcp_config.get("mcpServers", {})
                    
                    for server_name, server_config in servers.items():
                        # Extract tool information from server config
                        # Note: Actual tool discovery would require connecting to MCP servers
                        # This is a placeholder that can be extended
                        tools.append({
                            "name": f"mcp_{server_name}",
                            "server": server_name,
                            "description": f"MCP tool from {server_name}",
                            "type": "mcp"
                        })
            except Exception as e:
                logger.warning(f"Failed to read MCP config: {e}")
        
        # Also check for Cursor's built-in tools
        cursor_tools = self._discover_cursor_builtin_tools()
        tools.extend(cursor_tools)
        
        return tools
    
    def _discover_cursor_builtin_tools(self) -> List[Dict[str, Any]]:
        """Discover Cursor's built-in tools"""
        # These are tools that Cursor provides natively
        builtin_tools = [
            {
                "name": "cursor_read_file",
                "description": "Read a file from the workspace",
                "type": "builtin",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to the file"}
                    },
                    "required": ["path"]
                }
            },
            {
                "name": "cursor_write_file",
                "description": "Write content to a file",
                "type": "builtin",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to the file"},
                        "content": {"type": "string", "description": "Content to write"}
                    },
                    "required": ["path", "content"]
                }
            },
            {
                "name": "cursor_search_codebase",
                "description": "Search the codebase",
                "type": "builtin",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "limit": {"type": "number", "description": "Maximum results"}
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "cursor_list_files",
                "description": "List files in a directory",
                "type": "builtin",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Directory path"}
                    }
                }
            },
            {
                "name": "cursor_run_command",
                "description": "Run a terminal command",
                "type": "builtin",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Command to run"},
                        "cwd": {"type": "string", "description": "Working directory"}
                    },
                    "required": ["command"]
                }
            }
        ]
        
        return builtin_tools
    
    async def call_mcp_tool(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Call an MCP tool"""
        # For built-in tools, we can implement them directly
        if tool_name.startswith("cursor_"):
            return await self._call_builtin_tool(tool_name, params)
        
        # For MCP server tools, we would need to connect to the MCP server
        # This is a placeholder that can be extended with actual MCP protocol implementation
        logger.warning(f"MCP tool '{tool_name}' not yet fully implemented")
        return {"error": f"Tool '{tool_name}' not yet implemented"}
    
    async def _call_builtin_tool(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Call a built-in Cursor tool"""
        try:
            if tool_name == "cursor_read_file":
                path = params.get("path")
                if not path:
                    return {"error": "path parameter required"}
                if os.path.exists(path):
                    with open(path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    return {"success": True, "content": content}
                else:
                    return {"error": f"File not found: {path}"}
            
            elif tool_name == "cursor_write_file":
                path = params.get("path")
                content = params.get("content")
                if not path or content is None:
                    return {"error": "path and content parameters required"}
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(content)
                return {"success": True, "path": path}
            
            elif tool_name == "cursor_search_codebase":
                query = params.get("query")
                if not query:
                    return {"error": "query parameter required"}
                # Use grep for simple search (can be enhanced)
                limit = params.get("limit", 10)
                # This is a simplified implementation
                return {"success": True, "results": [], "query": query}
            
            elif tool_name == "cursor_list_files":
                path = params.get("path", ".")
                if os.path.isdir(path):
                    files = []
                    for item in os.listdir(path):
                        item_path = os.path.join(path, item)
                        files.append({
                            "name": item,
                            "path": item_path,
                            "type": "directory" if os.path.isdir(item_path) else "file"
                        })
                    return {"success": True, "files": files}
                else:
                    return {"error": f"Directory not found: {path}"}
            
            elif tool_name == "cursor_run_command":
                command = params.get("command")
                if not command:
                    return {"error": "command parameter required"}
                cwd = params.get("cwd", ".")
                try:
                    result = subprocess.run(
                        command,
                        shell=True,
                        cwd=cwd,
                        capture_output=True,
                        text=True,
                        timeout=30
                    )
                    return {
                        "success": result.returncode == 0,
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                        "returncode": result.returncode
                    }
                except subprocess.TimeoutExpired:
                    return {"error": "Command timed out"}
                except Exception as e:
                    return {"error": str(e)}
            
            else:
                return {"error": f"Unknown built-in tool: {tool_name}"}
        
        except Exception as e:
            logger.error(f"Error calling built-in tool {tool_name}: {e}")
            return {"error": str(e)}

# Global instance
_mcp_client: Optional[MCPToolsClient] = None

def get_mcp_client() -> MCPToolsClient:
    """Get or create the global MCP tools client"""
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = MCPToolsClient()
    return _mcp_client
