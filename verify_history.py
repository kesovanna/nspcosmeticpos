import os

history_dir = os.path.join(os.environ['APPDATA'], 'Code', 'User', 'History')
candidate = os.path.join(history_dir, '-2486492', 'WrK1.html')
with open(candidate, 'rb') as f:
    data = f.read()
text = data.decode('utf-8')
print('=== WrK1.html FULL VERIFICATION ===')
print('Size:', len(data), 'bytes')
print('Has BOM:', data[:3] == b'\xef\xbb\xbf')
print('DOCTYPE:', text.count('<!DOCTYPE html>'))
print('<body>:', text.count('<body'))
print('</body>:', text.count('</body>'))
print('</html>:', text.count('</html>'))
print('sidebar:', text.count('aside class="sidebar"'))
print('content-wrapper:', text.count('content-wrapper" style'))
print('Khmer:', len([c for c in text if '\u1780' <= c <= '\u17ff']))
print('Lines:', text.count(chr(10)))
for v in ['posView','reportsView','inventoryView','barcodeView','terminologyView','activitiesView','usersView','notificationsView','addProductView']:
    if v in text:
        print('  HAS:', v)
print('Ending:', repr(text[-60:]))
