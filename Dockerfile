FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ curl libgomp1 && \
    rm -rf /var/lib/apt/lists/*

# Python deps
COPY backend/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# App code
COPY backend/ ./backend/
COPY data/ ./data/

WORKDIR /app/backend

# Create init files
RUN touch engine/__init__.py vision/__init__.py rag/__init__.py tools/__init__.py

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
