# ParkiStep Twin - Production Container Image
FROM python:3.12-slim

# Prevent Python from writing bytecode and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR /app

# Install minimal OS dependencies for numerical routines and health checks
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install pinned Python dependencies
COPY requirements-lock.txt requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements-lock.txt

# Copy application source, configs, and documentation
COPY pyproject.toml LICENSE THIRD_PARTY_NOTICES.md README.md ./
COPY app/ ./app/
COPY configs/ ./configs/
COPY docs/ ./docs/
COPY reports/ ./reports/
COPY scripts/ ./scripts/
COPY src/ ./src/

# Expose Streamlit default port
EXPOSE 8501

# Container healthcheck using Streamlit built-in health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

# Launch Clinician Dashboard
ENTRYPOINT ["streamlit", "run", "app/Home.py"]
CMD ["--server.port=8501", "--server.address=0.0.0.0"]
