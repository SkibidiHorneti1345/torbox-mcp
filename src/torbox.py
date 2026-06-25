import os
import httpx
import logging
import asyncio
import tempfile
from urllib.parse import urlparse, urlunparse
from typing import Any, Dict

logger = logging.getLogger(__name__)

TORBOX_API_KEY = os.environ.get("TORBOX_API_KEY", "")
TORBOX_BASE_URL = "https://api.torbox.app/v1/api"
PROWLARR_URL = os.environ.get("PROWLARR_URL", "http://localhost:9696").rstrip("/")
MAX_CONCURRENT_TORBOX_OPS = int(os.environ.get("MAX_CONCURRENT_TORBOX_OPS", "3"))
TORBOX_BUSY_TIMEOUT = float(os.environ.get("TORBOX_BUSY_TIMEOUT", "0.25"))
TORRENT_SPOOL_MEMORY_BYTES = int(os.environ.get("TORRENT_SPOOL_MEMORY_BYTES", str(1024 * 1024)))

_torbox_semaphore = asyncio.Semaphore(MAX_CONCURRENT_TORBOX_OPS)

def get_headers() -> dict:
    if not TORBOX_API_KEY:
        raise ValueError("TORBOX_API_KEY is not configured.")
    return {
        "Authorization": f"Bearer {TORBOX_API_KEY}",
        "Accept": "application/json"
    }


def _build_client() -> httpx.AsyncClient:
    limits = httpx.Limits(
        max_connections=MAX_CONCURRENT_TORBOX_OPS,
        max_keepalive_connections=MAX_CONCURRENT_TORBOX_OPS,
    )
    return httpx.AsyncClient(limits=limits)


async def _acquire_torbox_slot() -> None:
    try:
        await asyncio.wait_for(_torbox_semaphore.acquire(), timeout=TORBOX_BUSY_TIMEOUT)
    except asyncio.TimeoutError as e:
        raise RuntimeError(
            "TorBox is busy handling other operations. Try again shortly."
        ) from e


def _redact_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.query:
        return url

    redacted_params = []
    for item in parsed.query.split("&"):
        key = item.split("=", 1)[0].lower()
        if key in {"apikey", "api_key", "token", "authorization"}:
            redacted_params.append(f"{item.split('=', 1)[0]}=REDACTED")
        else:
            redacted_params.append(item)
    return urlunparse(parsed._replace(query="&".join(redacted_params)))


def _normalize_prowlarr_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("HTTP torrent links must be valid absolute URLs.")

    configured = urlparse(PROWLARR_URL)
    configured_port = configured.port or (443 if configured.scheme == "https" else 80)
    incoming_port = parsed.port or (443 if parsed.scheme == "https" else 80)

    if parsed.hostname in {"localhost", "127.0.0.1", "::1"} and incoming_port == 9696:
        parsed = parsed._replace(scheme=configured.scheme, netloc=configured.netloc)

    parsed_port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if parsed.hostname != configured.hostname or parsed_port != configured_port:
        raise RuntimeError("Only magnet links or configured Prowlarr download URLs are accepted.")

    return urlunparse(parsed)


async def _post_magnet(client: httpx.AsyncClient, endpoint: str, magnet_link: str) -> Dict[str, Any]:
    response = await client.post(
        endpoint,
        headers=get_headers(),
        data={"magnet": magnet_link},
        timeout=10.0,
    )
    return _json_response(response, "TorBox magnet upload")


async def _download_torrent_to_file(
    client: httpx.AsyncClient,
    url: str,
    torrent_file,
    *,
    follow_redirects: bool,
) -> None:
    async with client.stream(
        "GET",
        url,
        timeout=15.0,
        follow_redirects=follow_redirects,
    ) as response:
        response.raise_for_status()
        async for chunk in response.aiter_bytes():
            torrent_file.write(chunk)


def _json_response(response: httpx.Response, action: str) -> Dict[str, Any]:
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as e:
        raise RuntimeError(f"{action} returned invalid JSON.") from e

    if not isinstance(data, dict):
        raise RuntimeError(f"{action} returned an unexpected response shape.")
    return data


def _format_http_error(e: httpx.HTTPStatusError, action: str) -> str:
    detail = e.response.text.strip()[:300] if e.response.text else ""
    if detail:
        return f"{action} failed with HTTP {e.response.status_code}: {detail}"
    return f"{action} failed with HTTP {e.response.status_code}."


def _format_request_error(e: Exception) -> str:
    return str(e).strip() or type(e).__name__

async def add_magnet(magnet_link: str) -> Dict[str, Any]:
    """
    Sends a magnet link or downloads a .torrent URL and uploads it to TorBox.
    POST /v1/api/torrents/createtorrent
    """
    endpoint = f"{TORBOX_BASE_URL}/torrents/createtorrent"
    await _acquire_torbox_slot()

    try:
        async with _build_client() as client:
            if magnet_link.startswith("magnet:"):
                return await _post_magnet(client, endpoint, magnet_link)

            if magnet_link.startswith("http://") or magnet_link.startswith("https://"):
                safe_url = _normalize_prowlarr_url(magnet_link)
                logger.info("Downloading torrent file from %s", _redact_url(safe_url))

                with tempfile.SpooledTemporaryFile(max_size=TORRENT_SPOOL_MEMORY_BYTES) as torrent_file:
                    async with client.stream(
                        "GET",
                        safe_url,
                        timeout=15.0,
                        follow_redirects=False,
                    ) as dl_resp:
                        if dl_resp.status_code in (301, 302, 303, 307, 308):
                            location = dl_resp.headers.get("Location", "")
                            if location.startswith("magnet:"):
                                return await _post_magnet(client, endpoint, location)

                            redirected_url = _normalize_prowlarr_url(location)
                            await _download_torrent_to_file(
                                client,
                                redirected_url,
                                torrent_file,
                                follow_redirects=True,
                            )
                        else:
                            dl_resp.raise_for_status()
                            async for chunk in dl_resp.aiter_bytes():
                                torrent_file.write(chunk)

                    torrent_file.seek(0)
                    files = {
                        "file": ("downloaded.torrent", torrent_file, "application/x-bittorrent")
                    }
                    response = await client.post(
                        endpoint,
                        headers=get_headers(),
                        files=files,
                        timeout=30.0,
                    )
                    return _json_response(response, "TorBox torrent upload")

            raise RuntimeError("Torrent link must be a magnet link or a configured Prowlarr download URL.")
    except httpx.TimeoutException as e:
        raise RuntimeError("Timed out while adding torrent to TorBox.") from e
    except httpx.HTTPStatusError as e:
        raise RuntimeError(_format_http_error(e, "TorBox torrent add")) from e
    except httpx.RequestError as e:
        raise RuntimeError(f"TorBox torrent add request failed: {_format_request_error(e)}.") from e
    finally:
        _torbox_semaphore.release()

async def get_torrent_list() -> Dict[str, Any]:
    """
    Fetches the user's torrent list.
    GET /v1/api/torrents/mylist
    """
    endpoint = f"{TORBOX_BASE_URL}/torrents/mylist"
    await _acquire_torbox_slot()

    try:
        async with _build_client() as client:
            response = await client.get(endpoint, headers=get_headers(), timeout=10.0)
            return _json_response(response, "TorBox torrent list")
    except httpx.TimeoutException as e:
        raise RuntimeError("Timed out while fetching TorBox torrent list.") from e
    except httpx.HTTPStatusError as e:
        raise RuntimeError(_format_http_error(e, "TorBox torrent list")) from e
    except httpx.RequestError as e:
        raise RuntimeError(f"TorBox torrent list request failed: {_format_request_error(e)}.") from e
    finally:
        _torbox_semaphore.release()

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
    await _acquire_torbox_slot()

    try:
        async with _build_client() as client:
            response = await client.post(endpoint, headers=get_headers(), json=data, timeout=10.0)
            return _json_response(response, "TorBox torrent control")
    except httpx.TimeoutException as e:
        raise RuntimeError("Timed out while controlling TorBox torrent.") from e
    except httpx.HTTPStatusError as e:
        raise RuntimeError(_format_http_error(e, "TorBox torrent control")) from e
    except httpx.RequestError as e:
        raise RuntimeError(f"TorBox torrent control request failed: {_format_request_error(e)}.") from e
    finally:
        _torbox_semaphore.release()

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
    await _acquire_torbox_slot()

    try:
        async with _build_client() as client:
            response = await client.get(endpoint, headers=get_headers(), params=params, timeout=10.0)
            data = _json_response(response, "TorBox download link request")
            
            # Typically data["data"] contains the download link
            if data.get("success") and "data" in data:
                return data["data"]
            raise RuntimeError(f"TorBox download link request failed: {data}")
    except httpx.TimeoutException as e:
        raise RuntimeError("Timed out while requesting TorBox download link.") from e
    except httpx.HTTPStatusError as e:
        raise RuntimeError(_format_http_error(e, "TorBox download link request")) from e
    except httpx.RequestError as e:
        raise RuntimeError(f"TorBox download link request failed: {_format_request_error(e)}.") from e
    finally:
        _torbox_semaphore.release()
