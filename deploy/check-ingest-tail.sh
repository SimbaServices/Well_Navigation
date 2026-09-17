#!/bin/bash
docker logs --tail 12 wellnav-ingest
docker ps --filter name=wellnav --format "{{.Names}} {{.Status}}"
