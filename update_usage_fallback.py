#!/usr/bin/env python3
"""
Update Claude Code and Codex CLI fallback usage values in config.yaml.

This script allows manual updating of usage data when browser CDP scraping
is not available. The fallback values are used by the dashboard to display
Claude Code and Codex CLI usage.

Usage:
    python update_usage_fallback.py claude --spend-used 24.50 --extra-balance 0.50
    python update_usage_fallback.py codex --weekly-remaining 75
    python update_usage_fallback.py --show
"""

import argparse
import yaml
import sys
from pathlib import Path
from datetime import datetime


def load_config():
    """Load config.yaml from the project directory."""
    config_path = Path(__file__).parent / "config.yaml"
    if not config_path.exists():
        print(f"Error: config.yaml not found at {config_path}")
        sys.exit(1)
    
    with open(config_path, "r") as f:
        return yaml.safe_load(f), config_path


def save_config(config, config_path):
    """Save config back to config.yaml."""
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)


def update_claude_fallback(args):
    """Update Claude Code fallback values."""
    config, config_path = load_config()
    
    if "claude_usage_fallback" not in config:
        config["claude_usage_fallback"] = {}
    
    fallback = config["claude_usage_fallback"]
    updated = []
    
    if args.spend_used is not None:
        fallback["spend_used"] = round(args.spend_used, 2)
        updated.append(f"spend_used=${args.spend_used}")
    
    if args.spend_limit is not None:
        fallback["spend_limit"] = round(args.spend_limit, 2)
        updated.append(f"spend_limit=${args.spend_limit}")
    
    if args.spend_reset_text:
        fallback["spend_reset_text"] = args.spend_reset_text
        updated.append(f"spend_reset_text={args.spend_reset_text}")
    
    if args.extra_balance is not None:
        fallback["extra_usage_balance"] = round(args.extra_balance, 2)
        updated.append(f"extra_usage_balance=${args.extra_balance}")
    
    if args.plan_usage_pct is not None:
        fallback["plan_usage_pct"] = args.plan_usage_pct
        updated.append(f"plan_usage_pct={args.plan_usage_pct}%")
    
    if args.weekly_pct is not None:
        fallback["weekly_pct"] = args.weekly_pct
        updated.append(f"weekly_pct={args.weekly_pct}%")
    
    if updated:
        fallback["last_updated"] = datetime.now().isoformat()
        fallback["source"] = "manual_update"
        save_config(config, config_path)
        print(f"✓ Updated Claude Code fallback values:")
        for item in updated:
            print(f"  - {item}")
        print(f"  - last_updated={fallback['last_updated']}")
    else:
        print("No values provided to update. Use --help for options.")


def update_codex_fallback(args):
    """Update Codex CLI fallback values."""
    config, config_path = load_config()
    
    if "codex_usage_fallback" not in config:
        config["codex_usage_fallback"] = {}
    
    fallback = config["codex_usage_fallback"]
    updated = []
    
    if args.five_hour_remaining is not None:
        fallback["five_hour_remaining_pct"] = args.five_hour_remaining
        updated.append(f"five_hour_remaining_pct={args.five_hour_remaining}%")
    
    if args.weekly_remaining is not None:
        fallback["weekly_remaining_pct"] = args.weekly_remaining
        updated.append(f"weekly_remaining_pct={args.weekly_remaining}%")
    
    if args.weekly_reset:
        fallback["weekly_reset"] = args.weekly_reset
        updated.append(f"weekly_reset={args.weekly_reset}")
    
    if updated:
        fallback["last_updated"] = datetime.now().isoformat()
        fallback["source"] = "manual_update"
        save_config(config, config_path)
        print(f"✓ Updated Codex CLI fallback values:")
        for item in updated:
            print(f"  - {item}")
        print(f"  - last_updated={fallback['last_updated']}")
    else:
        print("No values provided to update. Use --help for options.")


def show_fallbacks():
    """Display current fallback values."""
    config, _ = load_config()
    
    print("Current Fallback Values")
    print("=" * 50)
    
    print("\n🤖 Claude Code (claude.ai/settings/usage):")
    claude = config.get("claude_usage_fallback", {})
    if claude:
        print(f"  spend_used: ${claude.get('spend_used', 'N/A')}")
        print(f"  spend_limit: ${claude.get('spend_limit', 'N/A')}")
        print(f"  spend_reset_text: {claude.get('spend_reset_text', 'N/A')}")
        print(f"  extra_usage_balance: ${claude.get('extra_usage_balance', 'N/A')}")
        print(f"  plan_usage_pct: {claude.get('plan_usage_pct', 'N/A')}")
        print(f"  weekly_pct: {claude.get('weekly_pct', 'N/A')}")
        print(f"  last_updated: {claude.get('last_updated', 'N/A')}")
    else:
        print("  No fallback values configured")
    
    print("\n🛠️  Codex CLI (chatgpt.com/codex/settings/usage):")
    codex = config.get("codex_usage_fallback", {})
    if codex:
        print(f"  five_hour_remaining_pct: {codex.get('five_hour_remaining_pct', 'N/A')}%")
        print(f"  weekly_remaining_pct: {codex.get('weekly_remaining_pct', 'N/A')}%")
        print(f"  weekly_reset: {codex.get('weekly_reset', 'N/A')}")
        print(f"  last_updated: {codex.get('last_updated', 'N/A')}")
    else:
        print("  No fallback values configured")
    
    print("\n" + "=" * 50)
    print("\nTo update these values:")
    print("  python update_usage_fallback.py claude --spend-used 24.50 --extra-balance 0.50")
    print("  python update_usage_fallback.py codex --weekly-remaining 75")


def main():
    parser = argparse.ArgumentParser(
        description="Update Claude Code and Codex CLI fallback usage values",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Show current fallback values
  python update_usage_fallback.py --show
  
  # Update Claude Code usage
  python update_usage_fallback.py claude --spend-used 24.50 --extra-balance 0.50
  
  # Update Codex CLI usage
  python update_usage_fallback.py codex --weekly-remaining 75 --five-hour-remaining 80
        """
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    # Claude command
    claude_parser = subparsers.add_parser("claude", help="Update Claude Code fallback values")
    claude_parser.add_argument("--spend-used", type=float, help="Amount spent this billing cycle")
    claude_parser.add_argument("--spend-limit", type=float, help="Monthly spend limit")
    claude_parser.add_argument("--spend-reset-text", type=str, help="Reset date text (e.g., 'Apr 1')")
    claude_parser.add_argument("--extra-balance", type=float, help="Extra usage balance remaining")
    claude_parser.add_argument("--plan-usage-pct", type=int, help="Plan usage percentage")
    claude_parser.add_argument("--weekly-pct", type=int, help="Weekly usage percentage")
    
    # Codex command
    codex_parser = subparsers.add_parser("codex", help="Update Codex CLI fallback values")
    codex_parser.add_argument("--five-hour-remaining", type=int, help="5-hour window remaining percentage")
    codex_parser.add_argument("--weekly-remaining", type=int, help="Weekly window remaining percentage")
    codex_parser.add_argument("--weekly-reset", type=str, help="Weekly reset text (e.g., 'Resets in 3 days')")
    
    # Show command
    parser.add_argument("--show", action="store_true", help="Show current fallback values")
    
    args = parser.parse_args()
    
    if args.show or args.command is None:
        show_fallbacks()
        return
    
    if args.command == "claude":
        update_claude_fallback(args)
    elif args.command == "codex":
        update_codex_fallback(args)


if __name__ == "__main__":
    main()
