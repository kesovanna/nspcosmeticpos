# Khmer Fonts for PDF Invoice Rendering

This directory contains TrueType font files required for proper Khmer text rendering in PDF invoices.

## Required Fonts

To fix Khmer text rendering issues, add the following `.ttf` files to this directory:

1. **KhmerOS_muollight.ttf** - Used for headers and shop name (ហាងគ្រឿងសម្អាង NSP)
   - Download from: https://github.com/khmeros/khmer-os-fonts or system fonts
   - Used in: `@font-face` with font-family: 'Khmer OS Moul Light'

2. **KhmerOS_battambang.ttf** - Used for body text and labels
   - Download from: https://github.com/khmeros/khmer-os-fonts or system fonts
   - Used in: `@font-face` with font-family: 'Khmer OS Battambang'

## Installation Steps

1. Download the Khmer OS fonts from the official repository:
   ```
   https://github.com/khmeros/khmer-os-fonts/releases
   ```

2. Extract the `.ttf` files and place them in this `fonts` directory.

3. The `invoice.html` template already has the `@font-face` declarations configured to use these fonts:
   - Falls back to local system fonts if `.ttf` files are not found
   - Falls back to Google Fonts (Moul, Kantumruy Pro) if local fonts fail

## Font Fallback Chain

The invoice template uses this font priority:

**For Headers (Shop Name):**
```
'Khmer OS Moul Light' → 'Moul' (Google Fonts) → 'Khmer OS' (system) → sans-serif
```

**For Body Text:**
```
'Khmer OS Battambang' → 'Kantumruy Pro' (Google Fonts) → sans-serif
```

## PDF Rendering

When printing the invoice to PDF:
- The browser will embed the fonts directly into the PDF file
- Khmer characters (ខ្មែរ) will render correctly on any device
- Print media query (`@media print`) ensures optimal rendering for 80mm thermal printers

## Testing

1. Open an invoice in the browser: `http://localhost:5000/invoice`
2. Click "បោះពុម្ព | Print" button
3. Select "Save as PDF" to test rendering
4. Verify that Khmer text (ហាងគ្រឿងសម្អាង NSP, labels, etc.) displays correctly

## Troubleshooting

If Khmer text still doesn't render:
1. Verify font files are in this directory with correct filenames
2. Clear browser cache: Ctrl+Shift+Delete
3. Restart the Flask server: `python start_app.py`
4. Try printing again
