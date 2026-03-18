#!/bin/bash
set -e

# Start FastAPI server that serves both API and static files
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1