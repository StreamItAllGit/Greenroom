# @module greenroom.mcp
# @purpose Package initializer for Greenroom local stdio and web HTTP MCP servers.
# @inputs MCP tool queries and drop envelopes (@depends-on greenroom.mcp.server, greenroom.mcp.web_mcp)
# @outputs Re-exported MCP server instances and factory (@exports mcp_server, create_web_mcp_server)
"""Greenroom MCP server package."""

from greenroom.mcp.server import mcp_server
from greenroom.mcp.web_mcp import create_web_mcp_server

__all__ = ["mcp_server", "create_web_mcp_server"]
