"""Vercel entry point — Program microservice (program graphs, code generation).

Required env vars in Vercel dashboard:
  DATABASE_URL, MONGODB_URI, CLERK_JWKS_URL
  HUGGINGFACE_HUB_TOKEN (or OPENAI_API_KEY / ANTHROPIC_API_KEY)
  XPLORE_CORS_ORIGINS=https://your-frontend.vercel.app
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from program.app import app  # noqa: F401
