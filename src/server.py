import asyncio
import logging
from typing import Literal
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

from torbox import add_magnet, get_torrent_list, control_torrent, request_download_link
from prowlarr import search_indexers as prowlarr_search
from guardrails import validate_file_tree

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("TorBox AI Gateway", host="0.0.0.0", port=8000)

@mcp.tool()
async def search_indexers(query: str, intent: Literal["media", "software"]) -> list[dict]:
    """
    Searches TorBox-configured indexers (via Prowlarr) to find a suitable torrent.
    Returns a list of matching torrents.
    WARNING TO AI: The returned 'torrent_url' or 'magnetUrl' might be an internal http://localhost:9696 URL instead of a magnet link.
    THIS IS EXPECTED AND PERFECTLY NORMAL. The add_to_cloud tool is specially programmed to handle these localhost links! Do NOT reject them.
    Automatically filters and ranks by health.
    Intent 'media' searches movies/tv. Intent 'software' restricts to safe categories.
    """
    logger.info(f"Searching indexers for '{query}' with intent '{intent}'")
    results = await prowlarr_search(query, intent)
    return results

@mcp.tool()
async def add_to_cloud(torrent_link: str) -> dict:
    """
    Sends a torrent to TorBox to begin the download.
    WARNING TO AI: This parameter CAN be a magnet link OR an internal http://localhost:9696 URL. 
    DO NOT WORRY if the link is a localhost HTTP link. The backend is designed to download the file directly from localhost and upload it to Torbox. Just pass exactly what you received!
    """
    logger.info("Adding torrent to TorBox")
    result = await add_magnet(torrent_link)
    return result

@mcp.tool()
async def inspect_file_tree(torrent_id: str, intent: Literal["media", "software"]) -> dict:
    """
    Mandatory metadata pause & validation. Loops checking TorBox mylist status for the torrent.
    Once metadata is retrieved (status is downloading, cached, or completed), it inspects the file tree.
    If the file tree contains banned executables (when intent is media), it deletes the torrent and aborts.
    Returns the file list if safe.
    """
    logger.info(f"Inspecting file tree for torrent_id {torrent_id} with intent {intent}")
    
    max_retries = 30
    retry_delay = 5  # seconds
    
    for _ in range(max_retries):
        mylist_response = await get_torrent_list()
        
        # Typically data is in mylist_response["data"]
        torrents = mylist_response.get("data", [])
        
        target_torrent = None
        for t in torrents:
            if str(t.get("id")) == str(torrent_id):
                target_torrent = t
                break
                
        if not target_torrent:
            raise Exception(f"Torrent {torrent_id} not found in TorBox list.")
            
        status = target_torrent.get("download_state", "").lower()
        
        if status in ["metadl", "checking", "paused"]:
            logger.info(f"Torrent {torrent_id} status is {status}, waiting...")
            await asyncio.sleep(retry_delay)
            continue
            
        # Status is likely downloading, cached, completed, seeding
        files = target_torrent.get("files", [])
        if not files:
            # Maybe files haven't populated yet, wait a bit
            await asyncio.sleep(retry_delay)
            continue
            
        # We have the files, validate them
        logger.info(f"Metadata retrieved. Found {len(files)} files. Validating against intent '{intent}'...")
        is_safe = validate_file_tree(files, intent)
        
        if not is_safe:
            logger.error("Malicious file detected in torrent! Issuing delete command to TorBox.")
            await control_torrent(torrent_id, "delete")
            raise Exception("ABORT: Malicious executable files detected in media torrent. Torrent has been deleted.")
            
        logger.info("File tree validation passed. Safe to proceed.")
        return {"status": "safe", "files": files, "torrent": target_torrent}
        
    raise Exception("Timeout waiting for torrent metadata.")

@mcp.tool()
async def get_secure_link(torrent_id: str, file_id: str) -> str:
    """
    Generates a secure 3-hour permalink for streaming or downloading a specific file.
    """
    logger.info(f"Requesting secure link for torrent_id {torrent_id}, file_id {file_id}")
    link = await request_download_link(torrent_id, file_id)
    return link

if __name__ == "__main__":
    # Start the FastMCP SSE server
    mcp.run(transport="sse", host="0.0.0.0", port=8000)
