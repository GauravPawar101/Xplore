"""Vercel entry point — RAG microservice (semantic search, embeddings).

Required env vars in Vercel dashboard:
  DATABASE_URL, MILVUS_URI, CLERK_JWKS_URL
  HUGGINGFACE_HUB_TOKEN (or HF_TOKEN1/2/3)
  XPLORE_CORS_ORIGINS=https://your-frontend.vercel.app
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rag.app import app  # noqa: F401
