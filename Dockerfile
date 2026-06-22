FROM python:3.12-slim

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./src/

# Run the FastMCP server using stdio by default, but it can be changed.
# For FastMCP using the `mcp` CLI:
ENTRYPOINT ["mcp", "run", "src/server.py"]
