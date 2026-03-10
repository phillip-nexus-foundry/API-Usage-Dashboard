"""Deep diagnosis: compare raw JSONL opus data to Claude ground truth."""
import json, glob, os
from collections import Counter

os.chdir("/home/agents/openclaw-local/core/agents/main/sessions")

opus_by_id = {}
for pattern in ['*.jsonl', '*.jsonl.reset.*']:
    for f in glob.glob(pattern):
        try:
            with open(f, 'r', encoding='utf-8', errors='replace') as fh:
                for line in fh:
                    try:
                        obj = json.loads(line.strip())
                        if obj.get('type') != 'message': continue
                        msg = obj.get('message', {})
                        if msg.get('role') != 'assistant': continue
                        if 'opus' not in msg.get('model', ''): continue
                        mid = obj.get('id', '')
                        usage = msg.get('usage', {})
                        opus_by_id[mid] = {
                            'input': usage.get('input', 0),
                            'output': usage.get('output', 0),
                            'cr': usage.get('cacheRead', 0),
                            'cw': usage.get('cacheWrite', 0),
                            'stop': msg.get('stopReason', ''),
                            'error': msg.get('error'),
                            'ts': msg.get('timestamp', 0),
                        }
                    except:
                        pass
        except:
            pass

calls = sorted(opus_by_id.values(), key=lambda x: x['ts'])
print(f"Unique opus calls: {len(calls)}")

# Stop reasons
stops = Counter(c['stop'] for c in calls)
print(f"Stop reasons: {dict(stops)}")
errors = [c for c in calls if c['error'] or c['stop'] == 'error']
print(f"Error calls: {len(errors)}")

# Zero-output calls
zero_out = [c for c in calls if c['output'] == 0]
print(f"Zero-output calls: {len(zero_out)}")
for z in zero_out[:5]:
    print(f"  in={z['input']} cr={z['cr']} cw={z['cw']} stop={z['stop']}")

# Valid calls
valid = [c for c in calls if c['output'] > 0]
print(f"\nValid calls (output>0): {len(valid)}")
t_in = sum(c['input'] for c in valid)
t_out = sum(c['output'] for c in valid)
t_cr = sum(c['cr'] for c in valid)
t_cw = sum(c['cw'] for c in valid)
in_side = t_in + t_cr + t_cw
print(f"Tokens: in={t_in} out={t_out} cr={t_cr} cw={t_cw} in_side={in_side}")
print(f"Claude: in_side=6,229,977 out=47,367")
print(f"Diff in_side: {in_side - 6229977}")
print(f"Diff output:  {t_out - 47367}")

# Cost calculation with per-call billing
cost_in = t_in * 15 / 1e6
cost_out = t_out * 75 / 1e6
cost_cr = t_cr * 1.50 / 1e6
cost_cw = t_cw * 18.75 / 1e6
total = cost_in + cost_out + cost_cr + cost_cw
print(f"\nCost (per-call cw billing):")
print(f"  input:       ${cost_in:.4f}")
print(f"  output:      ${cost_out:.4f}")
print(f"  cache_read:  ${cost_cr:.4f}")
print(f"  cache_write: ${cost_cw:.4f}")
print(f"  TOTAL:       ${total:.4f}")

# What if Anthropic doesn't charge for cache_write and treats it as regular input?
cost_cw_as_input = t_cw * 15 / 1e6
total2 = cost_in + cost_out + cost_cr + cost_cw_as_input
print(f"\nCost (cw at input rate $15/MTok):")
print(f"  TOTAL:       ${total2:.4f}")

# What if we use Anthropic's actual billing from their API?
# Anthropic bills: input*15 + cr*1.875 + cw*18.75 + out*75 (wait, check 1.875 vs 1.50)
cost_cr_alt = t_cr * 1.875 / 1e6
total3 = cost_in + cost_out + cost_cr_alt + cost_cw
print(f"\nCost (cr at $1.875/MTok):")
print(f"  TOTAL:       ${total3:.4f}")

print(f"\nGround truth total Anthropic spend: $19.76")

# Time-based breakdown
from datetime import datetime
print("\n=== Daily Breakdown ===")
by_day = {}
for c in valid:
    day = datetime.utcfromtimestamp(c['ts']/1000).strftime('%Y-%m-%d')
    if day not in by_day:
        by_day[day] = {'calls': 0, 'in': 0, 'out': 0, 'cr': 0, 'cw': 0}
    by_day[day]['calls'] += 1
    by_day[day]['in'] += c['input']
    by_day[day]['out'] += c['output']
    by_day[day]['cr'] += c['cr']
    by_day[day]['cw'] += c['cw']

for day in sorted(by_day):
    d = by_day[day]
    in_side = d['in'] + d['cr'] + d['cw']
    cost = d['in']*15/1e6 + d['out']*75/1e6 + d['cr']*1.50/1e6 + d['cw']*18.75/1e6
    print(f"  {day}: calls={d['calls']} in_side={in_side} out={d['out']} cost=${cost:.4f}")
