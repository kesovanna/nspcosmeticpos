import re

with open('templates/index.html', 'r', encoding='utf-8') as f:
    lines = f.readlines()

print('=== LINES WITH JINJA INSIDE style ATTRIBUTES ===')
for i, line in enumerate(lines, 1):
    # Find style="..." attributes containing {{ or {%
    for m in re.finditer(r'style="([^"]*)"', line):
        attr = m.group(1)
        if '{{' in attr or '{%' in attr:
            print(f'Line {i}: style contains Jinja')
            print(f'   {line.strip()[:220]}')
            print()
