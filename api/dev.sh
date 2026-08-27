#!/bin/bash

exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 --reload --no-proxy-headers --timeout-graceful-shutdown 0
