#!/usr/bin/env bash
# Deploy Toolsmith into a CreateOS sandbox and serve it on its public ingress URL.
#   ./deploy.sh <sandbox-id>
# Needs the createos CLI and a local .env (see .env.example). No secret is ever placed
# on a command line: keys travel as an uploaded file that ends up root-only (chmod 600).
set -euo pipefail
SB="${1:?usage: ./deploy.sh <sandbox-id>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
set -a; source "$HERE/.env"; set +a
export CREATEOS_API_KEY

clean(){ sed 's/\x1b\[[0-9;]*m//g' | grep -vE '█|╗|╝|║|╚|╔|^\s*$' || true; }
x(){ createos sandbox exec "$SB" -- bash -lc "$1" 2>&1 | clean; }

echo "== 1. egress allowlist (in-kernel, set from outside the VM)"
createos sandbox firewall set "$SB" api.tokenrouter.com api.stripe.com pypi.org files.pythonhosted.org 2>&1 | clean
createos sandbox edit "$SB" --ingress on --auto-pause off 2>&1 | clean

echo "== 2. upload code + secrets file"
tar -C "$HERE" -czf "$TMP/app.tgz" src web requirements.txt
{
  echo "TOKENROUTER_API_KEY=$TOKENROUTER_API_KEY"
  echo "LLM_BASE_URL=${LLM_BASE_URL:-https://api.tokenrouter.com/v1}"
  echo "STRIPE_SECRET_KEY=$STRIPE_SECRET_KEY"
  echo "TOOLSMITH_MODEL=${TOOLSMITH_MODEL:-anthropic/claude-sonnet-5.5}"
} > "$TMP/toolsmith.env"
# Git Bash on Windows rewrites /abs paths; pass native local paths and keep remote paths literal.
lp(){ cygpath -w "$1" 2>/dev/null || echo "$1"; }
MSYS_NO_PATHCONV=1 createos sandbox push "$SB" "$(lp "$TMP/app.tgz")" /root/app.tgz 2>&1 | clean
MSYS_NO_PATHCONV=1 createos sandbox push "$SB" "$(lp "$TMP/toolsmith.env")" /root/toolsmith.env 2>&1 | clean

echo "== 3. install + lock down"
x 'set -e
PY=$(ls -d /root/.asdf/installs/python/*/bin/python3 | head -1)
chmod 600 /root/toolsmith.env
mkdir -p /opt/toolsmith && tar -xzf /root/app.tgz -C /opt/toolsmith
$PY -m pip install --quiet fastapi uvicorn openai python-dotenv requests
id forge >/dev/null 2>&1 || useradd -m forge
mkdir -p /srv/tools /srv/runs /srv/pylib
chown -R forge:forge /srv/runs /srv/pylib; chmod 755 /srv /srv/tools
chmod 711 /root; chmod -R o+rX /root/.asdf/installs
echo "python: $PY"'

echo "== 4. (re)start server on :8080"
x 'pkill -f "[h]ttp.server 8080" || true; pkill -f "[u]vicorn src.web_app" || true; echo stopped'
x 'PY=$(ls -d /root/.asdf/installs/python/*/bin/python3 | head -1)
cd /opt/toolsmith
set -a; . /root/toolsmith.env; set +a
export TOOLSMITH_PYTHON=$PY
setsid nohup $PY -m uvicorn src.web_app:app --host 0.0.0.0 --port 8080 >/var/log/toolsmith.log 2>&1 < /dev/null &
sleep 5; curl -s -o /dev/null -w "local=%{http_code}
" http://127.0.0.1:8080/api/meta; tail -3 /var/log/toolsmith.log'

ID="${SB#sb-}"
echo "== live: https://${ID}-8080.app.sb.createos.sh"
