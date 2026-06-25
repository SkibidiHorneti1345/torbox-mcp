import asyncio
import logging
import os
from typing import Literal
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

from torbox import add_magnet, get_torrent_list, control_torrent, request_download_link
from prowlarr import search_indexers as prowlarr_search
from guardrails import validate_file_tree

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# FastMCP defaults to 127.0.0.1. That is safe for local development, but makes
# the server unreachable through a Docker port mapping. Keep the container-safe
# defaults here and expose environment overrides for non-Docker deployments.
MCP_HOST = os.environ.get("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.environ.get("MCP_PORT", "8000"))
METADATA_MAX_RETRIES = int(os.environ.get("METADATA_MAX_RETRIES", "30"))
METADATA_RETRY_DELAY = float(os.environ.get("METADATA_RETRY_DELAY", "5"))

mcp = FastMCP("TorBox AI Gateway", host=MCP_HOST, port=MCP_PORT)


def _non_empty_error(prefix: str, error: Exception) -> RuntimeError:
    detail = str(error).strip() or type(error).__name__
    return RuntimeError(f"{prefix}: {detail}")

@mcp.tool()
async def search_indexers(query: str, intent: Literal["media", "software"]) -> list[dict]:
    """
    Searches TorBox-configured indexers (via Prowlarr) to find a suitable torrent.
    Returns a list of matching torrents.
    WARNING TO AI: The returned 'torrent_url' or 'magnetUrl' might be an internal Prowlarr URL instead of a magnet link.
    THIS IS EXPECTED AND PERFECTLY NORMAL. The add_to_cloud tool is specially programmed to handle these Prowlarr links. Do NOT reject them.
    Automatically filters and ranks by health.
    Intent 'media' searches movies/tv. Intent 'software' restricts to safe categories.
    """
    logger.info(f"Searching indexers for '{query}' with intent '{intent}'")
    try:
        return await prowlarr_search(query, intent)
    except RuntimeError:
        raise
    except Exception as e:
        raise _non_empty_error("Indexer search failed", e) from e

@mcp.tool()
async def add_to_cloud(torrent_link: str) -> dict:
    """
    Sends a torrent to TorBox to begin the download.
    WARNING TO AI: This parameter CAN be a magnet link OR an internal Prowlarr URL.
    The backend is designed to download configured Prowlarr torrent files and upload them to TorBox. Just pass exactly what you received from search_indexers.
    """
    logger.info("Adding torrent to TorBox")
    try:
        return await add_magnet(torrent_link)
    except RuntimeError:
        raise
    except Exception as e:
        raise _non_empty_error("Adding torrent to TorBox failed", e) from e

@mcp.tool()
async def inspect_file_tree(torrent_id: str, intent: Literal["media", "software"]) -> dict:
    """
    Mandatory metadata pause & validation. Loops checking TorBox mylist status for the torrent.
    Once metadata is retrieved (status is downloading, cached, or completed), it inspects the file tree.
    If the file tree contains banned executables (when intent is media), it deletes the torrent and aborts.
    Returns the file list if safe.
    """
    logger.info(f"Inspecting file tree for torrent_id {torrent_id} with intent {intent}")
    
    try:
        for _ in range(METADATA_MAX_RETRIES):
            mylist_response = await get_torrent_list()
            
            # Typically data is in mylist_response["data"]
            torrents = mylist_response.get("data", [])
            if not isinstance(torrents, list):
                raise RuntimeError("TorBox torrent list returned an unexpected response shape.")
            
            target_torrent = None
            for t in torrents:
                if isinstance(t, dict) and str(t.get("id")) == str(torrent_id):
                    target_torrent = t
                    break
                    
            if not target_torrent:
                raise RuntimeError(f"Torrent {torrent_id} not found in TorBox list.")
                
            status = target_torrent.get("download_state", "").lower()
            
            if status in ["metadl", "checking", "paused"]:
                logger.info(f"Torrent {torrent_id} status is {status}, waiting...")
                await asyncio.sleep(METADATA_RETRY_DELAY)
                continue
                
            # Status is likely downloading, cached, completed, seeding
            files = target_torrent.get("files", [])
            if not files:
                # Maybe files haven't populated yet, wait a bit
                await asyncio.sleep(METADATA_RETRY_DELAY)
                continue
                
            # We have the files, validate them
            logger.info(f"Metadata retrieved. Found {len(files)} files. Validating against intent '{intent}'...")
            is_safe = validate_file_tree(files, intent)
            
            if not is_safe:
                logger.error("Malicious file detected in torrent! Issuing delete command to TorBox.")
                await control_torrent(torrent_id, "delete")
                raise RuntimeError("ABORT: Malicious executable files detected in media torrent. Torrent has been deleted.")
                
            logger.info("File tree validation passed. Safe to proceed.")
            return {"status": "safe", "files": files, "torrent": target_torrent}
            
        raise RuntimeError("Timeout waiting for torrent metadata.")
    except RuntimeError:
        raise
    except Exception as e:
        raise _non_empty_error("Inspecting torrent metadata failed", e) from e

@mcp.tool()
async def get_secure_link(torrent_id: str, file_id: str) -> str:
    """
    Generates a secure 3-hour permalink for streaming or downloading a specific file.
    """
    logger.info(f"Requesting secure link for torrent_id {torrent_id}, file_id {file_id}")
    try:
        return await request_download_link(torrent_id, file_id)
    except RuntimeError:
        raise
    except Exception as e:
        raise _non_empty_error("Requesting secure link failed", e) from e

if __name__ == "__main__":
    # Start the FastMCP SSE server
    logger.info("Starting TorBox MCP SSE server on http://%s:%s/sse", MCP_HOST, MCP_PORT)
    mcp.run(transport="sse")
