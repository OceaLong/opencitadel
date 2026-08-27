#!/bin/bash

# Keep uvicorn as PID 1 and use the same bounded drain window as TaskSupervisor.
exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 --no-proxy-headers --timeout-graceful-shutdown "${OPENCITADEL_SHUTDOWN_TIMEOUT_SECONDS:-30}"
