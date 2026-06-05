#!/usr/bin/env bash
# Friendly log viewer for Salazar Trader.
#
# Works whether the bot runs under systemd (journalctl) or writes to the
# log file at /var/log/salazar-trader.log. Prefers the file if present.
#
# Usage:
#   scripts/logs.sh            # live tail (follow), default
#   scripts/logs.sh live       # same as above
#   scripts/logs.sh tail [N]   # last N lines (default 100), no follow
#   scripts/logs.sh errors     # only warnings/errors, live
#   scripts/logs.sh trades     # only trade/decision activity, live
#   scripts/logs.sh news       # only news/LLM activity, live
#   scripts/logs.sh today      # everything logged today, no follow
#   scripts/logs.sh status     # systemd service status
#   scripts/logs.sh help       # this help

set -euo pipefail

SERVICE="salazar-trader"
LOG_FILE="/var/log/salazar-trader.log"

GREEN='\033[0;32m'; YELLOW='\033[0;33m'; CYAN='\033[0;36m'; NC='\033[0m'

have_file() { [ -r "$LOG_FILE" ]; }
have_journal() { command -v journalctl >/dev/null 2>&1; }

# Source of log lines: file tail or journalctl. $1 = follow (yes/no).
stream() {
  local follow="$1"
  if have_file; then
    if [ "$follow" = "yes" ]; then
      tail -n 200 -f "$LOG_FILE"
    else
      cat "$LOG_FILE"
    fi
  elif have_journal; then
    if [ "$follow" = "yes" ]; then
      journalctl -u "$SERVICE" -n 200 -f --no-hostname
    else
      journalctl -u "$SERVICE" --no-hostname
    fi
  else
    echo "No log source found (neither $LOG_FILE nor journalctl)." >&2
    exit 1
  fi
}

cmd="${1:-live}"

case "$cmd" in
  live|"")
    echo -e "${GREEN}== Salazar Trader — live logs (Ctrl+C to stop) ==${NC}"
    stream yes
    ;;
  tail)
    n="${2:-100}"
    echo -e "${GREEN}== Last $n lines ==${NC}"
    if have_file; then tail -n "$n" "$LOG_FILE"
    else journalctl -u "$SERVICE" -n "$n" --no-hostname --no-pager; fi
    ;;
  errors|error)
    echo -e "${YELLOW}== Warnings & errors (live) ==${NC}"
    stream yes | grep --line-buffered -iE "error|warning|fail|traceback|exception|halt"
    ;;
  trades|trade|decisions)
    echo -e "${CYAN}== Trades & decisions (live) ==${NC}"
    stream yes | grep --line-buffered -iE "order|trade|decision|buy|sell|filled|position|execution"
    ;;
  news|llm)
    echo -e "${CYAN}== News & LLM activity (live) ==${NC}"
    stream yes | grep --line-buffered -iE "news|fetched|nlp|llm|classif|hot_ticker|movers|universe"
    ;;
  today)
    echo -e "${GREEN}== Today's logs ==${NC}"
    if have_journal && ! have_file; then
      journalctl -u "$SERVICE" --since today --no-hostname --no-pager
    else
      grep "$(date +%Y-%m-%d)" "$LOG_FILE" || echo "(no entries for today yet)"
    fi
    ;;
  status)
    systemctl status "$SERVICE" --no-pager || true
    ;;
  help|-h|--help)
    grep '^#' "$0" | sed 's/^# \{0,1\}//'
    ;;
  *)
    echo "Unknown command: $cmd" >&2
    echo "Try: scripts/logs.sh help" >&2
    exit 1
    ;;
esac
