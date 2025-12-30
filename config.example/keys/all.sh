# Source all API keys
# Copy this file to config/keys/all.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "$SCRIPT_DIR/openai.sh" 2>/dev/null || true
source "$SCRIPT_DIR/anthropic.sh" 2>/dev/null || true
source "$SCRIPT_DIR/gemini.sh" 2>/dev/null || true
