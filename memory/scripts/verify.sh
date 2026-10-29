#!/bin/sh
set -eu
base_url="${1:-http://127.0.0.1:8377}"
curl -fsS "$base_url/health"
curl -fsS "$base_url/status"

