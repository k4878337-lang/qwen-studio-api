# ==============================================================
# Qwen Studio - 24/7 OpenAI API Server Container
# Deployable to Hugging Face Spaces (Port 7860) & Render ($PORT)
# ==============================================================

FROM python:3.11-slim

WORKDIR /app

# Ensure non-buffered stdout for real-time logging
ENV PYTHONUNBUFFERED=1
ENV IS_CLOUD=1
ENV PORT=7860

# Install curl for container health check
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all project source code
COPY . .

# Expose standard HF Spaces port
EXPOSE 7860

# Run server
CMD ["python", "server.py"]
