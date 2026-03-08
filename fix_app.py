import re

with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add helper function before @app.get('/api/resources')
helper_func = '''def _get_claude_code_tier_display() -> str:
    """Get Claude Code tier display name from env var or config."""
    # Check env var first (CLAUDE_CODE_TIER=pro|max_100|max_200)
    tier = os.environ.get('CLAUDE_CODE_TIER', '').lower().strip()
    if not tier:
        # Fall back to config
        tier = CONFIG.get('claude_code_tier', 'pro').lower().strip()
    
    tier_map = {
        'pro': 'Claude Code Pro ($20/mo)',
        'max_100': 'Claude Code Max ($100/mo)',
        'max_200': 'Claude Code Max ($200/mo)',
    }
    return tier_map.get(tier, 'Claude Code Pro ($20/mo)')

'''

content = content.replace(
    '@app.get("/api/resources")\nasync def resources():',
    helper_func + '@app.get("/api/resources")\nasync def resources():'
)

# 2. Add _reload_config_from_disk() call in resources()
content = content.replace(
    'async def resources():\n    """Resource availability cards with 5-hour and 1-week usage windows."""\n    provider_defs = {',
    'async def resources():\n    """Resource availability cards with 5-hour and 1-week usage windows."""\n    _reload_config_from_disk()\n    provider_defs = {'
)

# 3. Use dynamic display name
content = content.replace(
    '"display_name": "Claude Code (Pro / Max)",',
    '"display_name": _get_claude_code_tier_display(),'
)

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Done')
