# EzDocs backend — used by microservices and monolith
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Default: run gateway (override in compose for graph_svc, rag_svc, program_svc)
CMD ["python", "gateway.py"]
