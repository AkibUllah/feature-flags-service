#!/usr/bin/env bash
# Codespaces start hook: bring up API + PostgreSQL + Redis and seed a demo project, flag and SDK key.
set -euo pipefail
cd "$(dirname "$0")/.."

docker compose up -d --build

echo "waiting for the API on :8000 ..."
for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

BASE=http://127.0.0.1:8000/v1
KEY=${ADMIN_API_KEY:-change-me}   # docker-compose.yml default
H=(-H "X-API-Key: $KEY" -H "X-Actor: codespace-seed" -H "Content-Type: application/json")

if [ ! -f .demo-sdk-key.txt ]; then
  curl -fsS -X POST "$BASE/projects" "${H[@]}" -d '{"key":"shop","name":"Dhaka Mart shop"}' >/dev/null
  curl -fsS -X POST "$BASE/projects/shop/flags" "${H[@]}" -d '{
    "key":"new-checkout","name":"New checkout","kind":"boolean",
    "variations":[{"key":"on","value":true},{"key":"off","value":false}]}' >/dev/null
  curl -fsS -X PATCH "$BASE/projects/shop/flags/new-checkout/environments/prod" "${H[@]}" -d '{
    "enabled": true,
    "targets": [{"variation":"on","keys":["staff_akib"]}],
    "rules": [{"id":"bd-modern","clauses":[
        {"attribute":"country","op":"eq","values":["BD"]},
        {"attribute":"app_version","op":"semver_gte","values":["2.4.0"]}],
      "variation":"on"}],
    "fallthrough": {"rollout":[{"variation":"on","weight":20000},{"variation":"off","weight":80000}]}}' >/dev/null
  curl -fsS -X POST "$BASE/projects/shop/environments/prod/sdk-keys" "${H[@]}" \
    -d '{"kind":"server","name":"codespace-demo"}' \
    | python3 -c 'import json,sys; print("server_sdk_key:", json.load(sys.stdin)["key"])' \
    | tee .demo-sdk-key.txt
fi

cat <<MSG

feature-flags-service is up.
  API docs:     forwarded port 8000, path /docs   (admin X-API-Key: $KEY)
  Seeded:       project "shop" (dev/staging/prod), flag "new-checkout" targeted in prod
  SDK key:      .demo-sdk-key.txt  -> POST /v1/evaluate with "Authorization: Bearer <key>"
MSG
