import os
import httpx
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

TORBOX_API_KEY = os.environ.get("TORBOX_API_KEY", "")
TORBOX_BASE_URL = "https://api.torbox.app/v1/api"

def get_headers() -> dict:
    if not TORBOX_API_KEY:
        raise ValueError("TORBOX_API_KEY is not configured.")
    return {
        "Authorization": f"Bearer {TORBOX_API_KEY}",
        "Accept": "application/json"
    }

async def add_magnet(magnet_link: str) -> Dict[str, Any]:
    """
    Sends a magnet link or downloads a .torrent URL and uploads it to TorBox.
    POST /v1/api/torrents/createtorrent
    """
    endpoint = f"{TORBOX_BASE_URL}/torrents/createtorrent"
    
    async with httpx.AsyncClient() as client:
        if magnet_link.startswith("http"):
            # It's a .torrent download URL (e.g., from Prowlarr proxy)
            # Prowlarr might return a 301 redirect to a magnet link
            dl_resp = await client.get(magnet_link, timeout=15.0, follow_redirects=False)
            
            if dl_resp.status_code in (301, 302, 303, 307, 308):
                location = dl_resp.headers.get("Location", "")
                if location.startswith("magnet:"):
                    data = {"magnet": location}
                    response = await client.post(endpoint, headers=get_headers(), data=data, timeout=10.0)
                    response.raise_for_status()
                    return response.json()
                else:
                    dl_resp = await client.get(location, timeout=15.0, follow_redirects=True)

            dl_resp.raise_for_status()
            torrent_bytes = dl_resp.content
            
            # 2. Upload it to TorBox
            files = {
                "file": ("downloaded.torrent", torrent_bytes, "application/x-bittorrent")
            }
            headers = get_headers()
            response = await client.post(endpoint, headers=headers, files=files, timeout=30.0)
        else:
            # It's a standard magnet link
            data = {"magnet": magnet_link}
            response = await client.post(endpoint, headers=get_headers(), data=data, timeout=10.0)
            
        response.raise_for_status()
        return response.json()

async def get_torrent_list() -> Dict[str, Any]:
    """
    Fetches the user's torrent list.
    GET /v1/api/torrents/mylist
    """
    endpoint = f"{TORBOX_BASE_URL}/torrents/mylist"
    
    async with httpx.AsyncClient() as client:
        response = await client.get(endpoint, headers=get_headers(), timeout=10.0)
        response.raise_for_status()
        return response.json()

async def control_torrent(torrent_id: str, operation: str) -> Dict[str, Any]:
    """
    Controls a torrent (e.g., delete, pause, resume).
    POST /v1/api/torrents/controltorrent
    """
    endpoint = f"{TORBOX_BASE_URL}/torrents/controltorrent"
    
    data = {
        "torrent_id": torrent_id,
        "operation": operation
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(endpoint, headers=get_headers(), json=data, timeout=10.0)
        response.raise_for_status()
        return response.json()

async def request_download_link(torrent_id: str, file_id: str) -> str:
    """
    Generates a secure 3-hour permalink for streaming/downloading.
    GET /v1/api/torrents/requestdl
    """
    endpoint = f"{TORBOX_BASE_URL}/torrents/requestdl"
    
    params = {
        "token": TORBOX_API_KEY,
        "torrent_id": torrent_id,
        "file_id": file_id
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.get(endpoint, headers=get_headers(), params=params, timeout=10.0)
        response.raise_for_status()
        data = response.json()
        
        # Typically data["data"] contains the download link
        if data.get("success") and "data" in data:
            return data["data"]
        else:
            raise Exception(f"Failed to get download link: {data}")
