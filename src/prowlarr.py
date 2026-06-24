import os
import httpx
import logging
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

# Prowlarr API configuration
PROWLARR_URL = os.environ.get("PROWLARR_URL", "http://localhost:9696").rstrip("/")
PROWLARR_API_KEY = os.environ.get("PROWLARR_API_KEY", "")

# We will use /api/v1/search as it is the standard for Prowlarr.
# (Note: Radarr/Sonarr use v3, Prowlarr uses v1)
SEARCH_ENDPOINT = f"{PROWLARR_URL}/api/v1/search"

async def search_indexers(query: str, intent: str) -> List[Dict[str, Any]]:
    """
    Searches Prowlarr for the given query.
    Filters zero seeders, and ranks results by max health (seeders).
    If intent is software, we can restrict to specific tags/categories.
    """
    if not PROWLARR_API_KEY:
        logger.error("PROWLARR_API_KEY is not set.")
        raise ValueError("PROWLARR_API_KEY is not configured.")

    logger.info("Querying Prowlarr at %s", SEARCH_ENDPOINT)

    headers = {
        "X-Api-Key": PROWLARR_API_KEY,
        "Accept": "application/json"
    }

    # Basic search parameters
    params = {
        "query": query,
        "type": "search"
    }

    # Category mappings (simplified example, usually Prowlarr uses Torznab category IDs)
    # 2000 = Movies, 5000 = TV, 4000 = PC (Software/Games)
    if intent == "software":
        params["categories"] = [4000]
    elif intent == "media":
        params["categories"] = [2000, 5000]

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(SEARCH_ENDPOINT, headers=headers, params=params, timeout=15.0)
            response.raise_for_status()
            results = response.json()
        except Exception as e:
            logger.error("Failed to query Prowlarr at %s: %r", SEARCH_ENDPOINT, e)
            raise

    # Filter and rank results
    valid_results = []
    for item in results:
        seeders = item.get("seeders", 0)
        magnet = item.get("magnetUrl") or item.get("downloadUrl")
        
        if seeders > 0 and magnet:
            # We want magnet links ideally, or direct torrent file URLs.
            # For TorBox, magnet links are preferred.
            valid_results.append({
                "title": item.get("title"),
                "seeders": seeders,
                "leechers": item.get("leechers", 0),
                "size": item.get("size", 0),
                "magnetUrl": magnet,
                "indexer": item.get("indexer")
            })

    # Sort by seeders descending
    valid_results.sort(key=lambda x: x["seeders"], reverse=True)
    
    # Return top 10 optimal results
    return valid_results[:10]
