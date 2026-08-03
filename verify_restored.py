with open('templates/index.html', 'rb') as f:
    data = f.read()
text = data.decode('utf-8')
print('=== RESTORED index.html VERIFICATION ===')
print('Size:', len(data), 'bytes')
print('Has BOM:', data[:3] == b'\xef\xbb\xbf')
print('DOCTYPE:', text.count('<!DOCTYPE html>'))
print('</body>:', text.count('</body>'))
print('</html>:', text.count('</html>'))
print('sidebar:', text.count('aside class="sidebar"'))
print('Khmer chars:', len([c for c in text if '\u1780' <= c <= '\u17ff']))
print('Lines:', text.count(chr(10)))
print('First 200 chars:', repr(text[:200]))
for v in ['posView','reportsView','inventoryView','barcodeView','terminologyView','activitiesView','usersView','notificationsView','addProductView']:
    if v in text:
        print('  HAS:', v)
print('Ending:', repr(text[-60:]))
