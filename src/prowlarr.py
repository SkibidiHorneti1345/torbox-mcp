import os
import httpx
import logging
import time
import asyncio
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

# Prowlarr API configuration
PROWLARR_URL = os.environ.get("PROWLARR_URL", "http://localhost:9696").rstrip("/")
PROWLARR_API_KEY = os.environ.get("PROWLARR_API_KEY", "")
PROWLARR_SEARCH_TIMEOUT = float(os.environ.get("PROWLARR_SEARCH_TIMEOUT", "120"))
MAX_CONCURRENT_PROWLARR_SEARCHES = int(os.environ.get("MAX_CONCURRENT_PROWLARR_SEARCHES", "5"))
PROWLARR_BUSY_TIMEOUT = float(os.environ.get("PROWLARR_BUSY_TIMEOUT", "0.25"))

# We will use /api/v1/search as it is the standard for Prowlarr.
# (Note: Radarr/Sonarr use v3, Prowlarr uses v1)
SEARCH_ENDPOINT = f"{PROWLARR_URL}/api/v1/search"
_search_semaphore = asyncio.Semaphore(MAX_CONCURRENT_PROWLARR_SEARCHES)


def _build_client() -> httpx.AsyncClient:
    timeout = httpx.Timeout(PROWLARR_SEARCH_TIMEOUT, connect=10.0)
    limits = httpx.Limits(
        max_connections=MAX_CONCURRENT_PROWLARR_SEARCHES,
        max_keepalive_connections=MAX_CONCURRENT_PROWLARR_SEARCHES,
    )
    return httpx.AsyncClient(timeout=timeout, limits=limits)


async def _acquire_search_slot() -> None:
    try:
        await asyncio.wait_for(_search_semaphore.acquire(), timeout=PROWLARR_BUSY_TIMEOUT)
    except asyncio.TimeoutError as e:
        raise RuntimeError(
            "Prowlarr is busy handling other searches. Try again shortly."
        ) from e


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
        "type": "search",
        "limit": 10,
        "offset": 0,
    }

    # Category mappings (simplified example, usually Prowlarr uses Torznab category IDs)
    # 2000 = Movies, 5000 = TV, 4000 = PC (Software/Games)
    if intent == "software":
        params["categories"] = [4000]
    elif intent == "media":
        params["categories"] = [2000, 5000]

    await _acquire_search_slot()

    started_at = time.monotonic()
    try:
        async with _build_client() as client:
            results = await _query_prowlarr(client, headers, params)
    finally:
        _search_semaphore.release()

    logger.info(
        "Prowlarr returned %d results in %.1f seconds",
        len(results),
        time.monotonic() - started_at,
    )

    # Filter and rank results
    valid_results = []
    for item in results:
        if not isinstance(item, dict):
            continue

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


async def _query_prowlarr(
    client: httpx.AsyncClient,
    headers: Dict[str, str],
    params: Dict[str, Any],
) -> List[Dict[str, Any]]:
    try:
        response = await client.get(SEARCH_ENDPOINT, headers=headers, params=params)
        response.raise_for_status()
        results = response.json()
        if not isinstance(results, list):
            raise ValueError(f"Expected a list response, got {type(results).__name__}.")
        return results
    except httpx.TimeoutException as e:
        message = (
            f"Prowlarr search timed out after {PROWLARR_SEARCH_TIMEOUT:g} seconds "
            f"while waiting for its configured indexers."
        )
        logger.error("%s Endpoint: %s", message, SEARCH_ENDPOINT)
        raise RuntimeError(message) from e
    except httpx.HTTPStatusError as e:
        message = f"Prowlarr returned HTTP {e.response.status_code} for the search request."
        logger.error("%s Endpoint: %s", message, SEARCH_ENDPOINT)
        raise RuntimeError(message) from e
    except httpx.RequestError as e:
        detail = str(e).strip() or type(e).__name__
        message = f"Could not connect to Prowlarr: {detail}."
        logger.error("%s Endpoint: %s", message, SEARCH_ENDPOINT)
        raise RuntimeError(message) from e
    except Exception as e:
        detail = str(e).strip() or type(e).__name__
        message = f"Prowlarr search failed: {detail}."
        logger.exception("%s Endpoint: %s", message, SEARCH_ENDPOINT)
        raise RuntimeError(message) from e
