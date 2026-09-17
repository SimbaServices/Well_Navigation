#!/bin/bash
set -eu
echo '==== containers ===='
docker ps -a
echo '==== ingest gone? ===='
docker ps -a --filter name=wellnav-ingest || true
echo '==== oom ===='
dmesg -T 2>/dev/null | grep -i -E 'killed process|out of memory|oom' | tail -20 || true
echo '==== memory ===='
free -h
echo '==== db ===='
docker exec -e PYTHONPATH=/app -w /app wellnav python deploy/check-operators.py
