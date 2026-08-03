import os

d = r'C:\Users\REAM KESOVANNA\AppData\Roaming\Code\User\History\-2486492'

for name in ['6yg3.html', 'GQpA.html', 'WrK1.html']:
    p = os.path.join(d, name)
    lines = open(p, encoding='utf-8').readlines()
    for i, l in enumerate(lines):
        if 'id="addProductModal"' in l:
            print(f'=== {name}: modal starts at line {i+1}, total {len(lines)} ===')
            print(''.join(lines[i:i+130]))
            break