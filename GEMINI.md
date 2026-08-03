# NSP Cosmetic POS

## Project Overview
A Point of Sale (POS) system for NSP Cosmetic, built with Python/Flask and integrated with Firebase.

## Technology Stack
- **Backend:** Python (Flask)
- **Frontend:** HTML/CSS (Jinja2 templates)
- **Database:** Firebase Firestore
- **Deployment:** Firebase Hosting/Functions (suggested by `.firebaserc` and `firebase.json`)

## Development Environment
- **Virtual Environment:** `.venv` is the primary virtual environment.
- **Dependencies:** Managed via `requirements.txt`.
- **Environment Variables:** Stored in `.env` (template provided in `.env.template`).

## Project Structure
- `main.py`: Primary application entry point.
- `templates/`: Jinja2 HTML templates.
- `static/`: Static assets (CSS, JS, images, sounds).
- `upload_products.py`: Script for bulk product uploads.

## Conventions
- **Coding Style:** Follow PEP 8 for Python code.
- **Security:** Never commit `serviceAccountKey.json` or `.env` files.
- **Testing:** (To be defined - no obvious test directory found yet)
