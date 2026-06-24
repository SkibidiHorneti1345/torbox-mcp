# TorBox MCP Server

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-blue" alt="Python">
  <img src="https://img.shields.io/badge/Framework-FastMCP-green" alt="Framework">
  <img src="https://img.shields.io/badge/License-MIT-lightgrey" alt="License">
</p>

A robust Model Context Protocol (MCP) server that connects your AI agents to Prowlarr and TorBox, allowing for fully autonomous torrent searching, downloading, and media extraction.

## Features

- Search for torrents across indexers configured in Prowlarr.
- Send torrents and magnet links directly to TorBox for secure cloud downloading.
- Automatically handles indexer redirects and localhost proxies (e.g., FlareSolverr).
- Inspects downloaded TorBox file trees to extract specific media files.
- Generates secure streaming/download links for downloaded files.
- Built-in guardrails to reject executable files from media requests for security.

## Requirements

- Python 3.10+
- TorBox Account (with API Key)
- Prowlarr Instance (Local or Remote)
- Docker (optional, for running Prowlarr/FlareSolverr via the included compose file)

## Setup

1. Clone the repository.
2. Create a virtual environment and install dependencies:
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env` and fill in your details:
   ```env
   PROWLARR_URL=http://localhost:9696
   PROWLARR_API_KEY=your_prowlarr_api_key_here
   TORBOX_API_KEY=your_torbox_api_key_here
   ```

4. Start the MCP server:
   ```bash
   python src/server.py
   ```

   The SSE endpoint is `http://localhost:8000/sse`. The server binds to
   `0.0.0.0:8000` by default so that it remains reachable through Docker port
   publishing. `MCP_HOST` and `MCP_PORT` may be used to override the bind
   address for non-Docker deployments.

   To run the complete stack in Docker:
   ```bash
   docker compose up --build -d
   ```

   In Docker, Prowlarr is reached by its Compose service name at
   `http://prowlarr:9696`; do not use `localhost` for this connection. The
   `localhost` value in the local `.env` example is only for running the MCP
   server directly on the host.

## Usage

The server exposes tools to your AI agent natively over MCP:
- `search_indexers`
- `add_to_cloud`
- `inspect_file_tree`
- `get_secure_link`

## License

MIT License
