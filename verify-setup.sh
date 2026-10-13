#!/bin/bash
# verify-setup.sh - Verify songbird-agent-mcp environment and prerequisites

set -e

echo "=========================================="
echo "  songbird-agent-mcp Setup Verification"
echo "=========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0

pass() {
    echo -e "${GREEN}✓${NC} $1"
    PASS_COUNT=$((PASS_COUNT + 1))
}

warn() {
    echo -e "${YELLOW}⚠${NC} $1"
    WARN_COUNT=$((WARN_COUNT + 1))
}

fail() {
    echo -e "${RED}✗${NC} $1"
    FAIL_COUNT=$((FAIL_COUNT + 1))
}

# 1. Check Python and uv
echo "--- Python/uv Environment ---"
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version | awk '{print $2}')
    pass "Python found: $PYTHON_VERSION"
else
    fail "Python 3 not found. Please install Python 3.10+."
fi

if command -v uv &> /dev/null; then
    UV_VERSION=$(uv --version | awk '{print $2}')
    pass "uv found: $UV_VERSION"
else
    warn "uv not found. Install via: curl -LsSf https://astral.sh/uv/install.sh | sh"
fi

echo ""

# 2. Check environment variables
echo "--- Environment Variables ---"

# Check SONGBIRD_AGENT_DB
if [ -n "$SONGBIRD_AGENT_DB" ]; then
    if [ "$SONGBIRD_AGENT_DB" = ":memory:" ]; then
        pass "SONGBIRD_AGENT_DB=:memory: (in-memory mode)"
    elif [ -d "$(dirname "$SONGBIRD_AGENT_DB")" ] || [ ! -e "$SONGBIRD_AGENT_DB" ]; then
        pass "SONGBIRD_AGENT_DB is set to: $SONGBIRD_AGENT_DB"
    else
        warn "SONGBIRD_AGENT_DB is set but directory may not exist: $(dirname "$SONGBIRD_AGENT_DB")"
    fi
else
    warn "SONGBIRD_AGENT_DB not set (will default to :memory:)"
fi

# Check ComfyUI configuration
echo ""
echo "--- ComfyUI Integration ---"

if [ -n "$COMFYUI_URL" ]; then
    pass "COMFYUI_URL is set: $COMFYUI_URL"
    
    # Test ComfyUI accessibility
    if command -v curl &> /dev/null; then
        HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$COMFYUI_URL/system_stats" 2>/dev/null || echo "000")
        if [ "$HTTP_CODE" = "200" ]; then
            pass "ComfyUI /system_stats endpoint is accessible (HTTP $HTTP_CODE)"
        elif [ "$HTTP_CODE" = "000" ]; then
            fail "ComfyUI /system_stats endpoint is not reachable (connection refused or timeout)"
        else
            warn "ComfyUI /system_stats returned HTTP $HTTP_CODE (expected 200)"
        fi
    else
        warn "curl not available, skipping ComfyUI connectivity test"
    fi
else
    warn "COMFYUI_URL not set - ComfyUI render adapters will not be available"
fi

if [ -n "$SONGBIRD_WORKFLOW_ROOT" ]; then
    if [ -d "$SONGBIRD_WORKFLOW_ROOT" ]; then
        pass "SONGBIRD_WORKFLOW_ROOT exists: $SONGBIRD_WORKFLOW_ROOT"
        
        # Check for required workflow files
        WORKFLOWS_OK=true
        for wf in "audio_ace_step_1_5_checkpoint.json" "audio_ace_step_m2m_edit.json" "audio_minimax_music_3.json"; do
            if [ -f "$SONGBIRD_WORKFLOW_ROOT/$wf" ]; then
                pass "  - Workflow file found: $wf"
            else
                fail "  - Missing workflow file: $wf"
                WORKFLOWS_OK=false
            fi
        done
        
        if [ "$WORKFLOWS_OK" = false ]; then
            warn "Some workflow files are missing. Place them in: $SONGBIRD_WORKFLOW_ROOT"
        fi
    else
        fail "SONGBIRD_WORKFLOW_ROOT does not exist: $SONGBIRD_WORKFLOW_ROOT"
    fi
else
    warn "SONGBIRD_WORKFLOW_ROOT not set - ComfyUI workflow files location unknown"
fi

if [ -n "$SONGBIRD_OUTPUT_DIR" ]; then
    if [ -d "$SONGBIRD_OUTPUT_DIR" ]; then
        pass "SONGBIRD_OUTPUT_DIR exists: $SONGBIRD_OUTPUT_DIR"
    else
        warn "SONGBIRD_OUTPUT_DIR does not exist: $SONGBIRD_OUTPUT_DIR (will be created on first render)"
    fi
fi

echo ""
echo "--- Demucs Stem Separation ---"

if [ -n "$SONGBIRD_DEMUCS_BIN" ]; then
    if command -v "$SONGBIRD_DEMUCS_BIN" &> /dev/null; then
        pass "SONGBIRD_DEMUCS_BIN found: $SONGBIRD_DEMUCS_BIN"
    else
        fail "SONGBIRD_DEMUCS_BIN is set but binary not found: $SONGBIRD_DEMUCS_BIN"
    fi
else
    warn "SONGBIRD_DEMUCS_BIN not set - demucs_stem adapter will not be available"
fi

# Check for ffprobe/ffmpeg
if command -v ffprobe &> /dev/null; then
    FFPROBE_VERSION=$(ffprobe -version | head -n 1 | awk '{print $3}')
    pass "ffprobe found: $FFPROBE_VERSION"
else
    warn "ffprobe not found. Install via: sudo apt-get install ffmpeg (Ubuntu/Debian) or brew install ffmpeg (macOS)"
fi

if command -v ffmpeg &> /dev/null; then
    FFMPEG_VERSION=$(ffmpeg -version | head -n 1 | awk '{print $3}')
    pass "ffmpeg found: $FFMPEG_VERSION"
else
    warn "ffmpeg not found. Install via: sudo apt-get install ffmpeg (Ubuntu/Debian) or brew install ffmpeg (macOS)"
fi

# Check for demucs binary in PATH if not explicitly configured
if [ -z "$SONGBIRD_DEMUCS_BIN" ]; then
    if command -v demucs &> /dev/null; then
        pass "demucs binary found in PATH"
    else
        warn "demucs binary not found in PATH. Install via: pip install demucs or conda install -c conda-forge demucs"
    fi
fi

echo ""
echo "--- Summary ---"
echo "Passes:   $PASS_COUNT"
echo "Warnings: $WARN_COUNT"
echo "Failures: $FAIL_COUNT"
echo ""

if [ $FAIL_COUNT -gt 0 ]; then
    echo -e "${RED}Some checks failed. Please review the output above.${NC}"
    echo "The MCP server may start but certain features will be unavailable."
    exit 1
elif [ $WARN_COUNT -gt 0 ]; then
    echo -e "${YELLOW}Some warnings were reported. The MCP server should start, but check warnings.${NC}"
    exit 0
else
    echo -e "${GREEN}All checks passed! The environment is ready for songbird-agent-mcp.${NC}"
    exit 0
fi
