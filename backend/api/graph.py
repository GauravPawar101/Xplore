"""Vercel entry point — Graph microservice (codebase analysis, file tree, graph persistence).

Vercel project root must be set to `backend/`.
Deploy: create a Vercel project, set Root Directory to `backend`, Framework to Other.
Required env vars in Vercel dashboard:
  DATABASE_URL, CLERK_JWKS_URL
  XPLORE_CORS_ORIGINS=https://your-frontend.vercel.app
"""
import sys
import os

# Ensure `backend/` is on the path so `from shared.* import ...` works
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graph.app import app  # noqa: F401  (Vercel detects `app` automatically)
