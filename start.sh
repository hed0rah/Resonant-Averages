#!/bin/bash
cd backend
pip install -r requirements.txt
cd ..
exec uvicorn backend.main:app --host 0.0.0.0 --port $PORT
