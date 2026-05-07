#!/usr/bin/env bash
# run.sh — end-to-end script to start infrastructure and run the agent
#
# Steps performed:
#   1. Verify .env exists and required API keys are set
#   2. Start the Docker infrastructure (otelcol-contrib + Grafana LGTM)
#   3. Wait for the OTEL collector to pass its health check
#   4. Create a Python venv (if absent) and install requirements
#   5. Run the agent
#   6. Print trace links for Grafana and Braintrust
#
# Usage:
#   chmod +x run.sh
#   ./run.sh
#
# To tear down the Docker stack afterwards:
#   docker compose down

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Colour helpers ─────────────────────────────────────────────────────────────
bold=$(tput bold 2>/dev/null || true)
reset=$(tput sgr0 2>/dev/null || true)
red=$(tput setaf 1 2>/dev/null || true)
green=$(tput setaf 2 2>/dev/null || true)
cyan=$(tput setaf 6 2>/dev/null || true)

info()  { echo "${cyan}${bold}[run.sh]${reset} $*"; }
ok()    { echo "${green}${bold}[run.sh]${reset} $*"; }
die()   { echo "${red}${bold}[run.sh] ERROR:${reset} $*" >&2; exit 1; }

# ── Step 1: API keys ───────────────────────────────────────────────────────────
info "Step 1: Checking API keys..."

if [[ ! -f .env ]]; then
    info ".env not found — copying from .env.example"
    cp .env.example .env
    die ".env created from template. Open it and fill in BRAINTRUST_API_KEY and OPENAI_API_KEY, then re-run this script."
fi

# Source the .env file so we can validate the values (without exporting to the
# current shell permanently). Using a subshell to avoid polluting the environment.
check_keys() {
    # shellcheck disable=SC1091
    set -a; source .env; set +a
    local missing=()
    [[ -z "${BRAINTRUST_API_KEY:-}" || "$BRAINTRUST_API_KEY" == *"your_"* ]] && missing+=("BRAINTRUST_API_KEY")
    [[ -z "${OPENAI_API_KEY:-}"    || "$OPENAI_API_KEY"    == *"your_"* ]] && missing+=("OPENAI_API_KEY")
    if [[ ${#missing[@]} -gt 0 ]]; then
        echo "${missing[*]}"
    fi
}

missing_keys=$(check_keys)
if [[ -n "$missing_keys" ]]; then
    die "The following keys are not set in .env: $missing_keys"$'\nOpen .env and fill them in, then re-run this script.'
fi

ok "API keys found."

# ── Step 2: Start Docker infrastructure ───────────────────────────────────────
info "Step 2: Starting Docker infrastructure (lgtm + collector)..."

# Pass .env values to docker compose. docker compose automatically reads .env
# from the project directory, so no explicit --env-file flag is needed here.
docker compose up -d

ok "Containers started."

# ── Step 3: Wait for the collector health check ────────────────────────────────
info "Step 3: Waiting for the OTEL collector to become healthy..."

COLLECTOR_HEALTH="http://localhost:13133/health"
MAX_WAIT=60   # seconds
INTERVAL=3

elapsed=0
while true; do
    if curl -sf "$COLLECTOR_HEALTH" > /dev/null 2>&1; then
        ok "Collector is healthy."
        break
    fi
    if (( elapsed >= MAX_WAIT )); then
        die "Collector did not become healthy after ${MAX_WAIT}s."$'\nCheck logs with: docker compose logs collector'
    fi
    info "  ...waiting (${elapsed}s elapsed)"
    sleep "$INTERVAL"
    elapsed=$(( elapsed + INTERVAL ))
done

# ── Step 4: Python venv + dependencies ────────────────────────────────────────
info "Step 4: Setting up Python environment..."

VENV_DIR="$SCRIPT_DIR/agent/.venv"
REQUIREMENTS="$SCRIPT_DIR/agent/requirements.txt"

if [[ ! -d "$VENV_DIR" ]]; then
    info "  Creating virtual environment at agent/.venv"
    python3 -m venv "$VENV_DIR"
fi

# Always run pip install so that added/updated packages are picked up without
# needing to manually delete the venv.
info "  Installing/updating Python dependencies"
"$VENV_DIR/bin/pip" install --quiet -r "$REQUIREMENTS"

ok "Python environment ready."

# ── Step 5: Run the agent ──────────────────────────────────────────────────────
info "Step 5: Running the agent..."
echo ""

# Export the .env vars so agent.py can read them via os.getenv / python-dotenv.
# set -a exports every variable assignment; set +a stops that.
set -a
# shellcheck disable=SC1091
source .env
set +a

"$VENV_DIR/bin/python" "$SCRIPT_DIR/agent/agent.py"

# ── Step 6: Print trace links ─────────────────────────────────────────────────
echo ""
ok "Done. View your traces:"
echo "  Grafana:    http://localhost:3000  →  Explore → Tempo → service: acme-bank-support-agent"
echo "  Braintrust: https://www.braintrust.dev  →  project: braintrust-otelcol-examples → Logs"
echo ""
info "Collector span counts:"
curl -sf "http://localhost:8888/metrics" | grep "otelcol_exporter_sent_spans" || true
echo ""
info "To stop the infrastructure: docker compose down"
