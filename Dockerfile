FROM python:3.12-slim

WORKDIR /app

# FastMCP otherwise defaults to the loopback interface, which cannot receive
# traffic forwarded through Docker's published port.
ENV MCP_HOST=0.0.0.0 \
    MCP_PORT=8000 \
    PROWLARR_URL=http://prowlarr:9696 \
    PROWLARR_SEARCH_TIMEOUT=120

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./src/

EXPOSE 8000

# Check the socket from inside the container so a loopback-only or failed
# server is surfaced as unhealthy instead of silently accepting a port map.
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os, socket; socket.create_connection((socket.gethostbyname(socket.gethostname()), int(os.environ['MCP_PORT'])), timeout=2).close()"

ENTRYPOINT ["python", "src/server.py"]
