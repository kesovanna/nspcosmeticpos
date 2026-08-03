# 🛍️ NSP Cosmetic Store - POS System

A custom-built Point of Sale (POS) application designed specifically to streamline daily retail operations, inventory management, and sales processing for NSP Cosmetic Store.

## 🚀 Key Features

*   **📊 Smart Dashboard:** Real-time overview of daily sales, total transactions, and top-selling products.
*   **🛒 Point of Sale (POS):** Fast and intuitive checkout interface with barcode scanning support.
*   **📦 Inventory Management:** Easily add, edit, and track product stock, categories, pricing, and expiration dates.
*   **💳 Digital Payments:** Seamless integration with **ABA PayWay** for quick and secure cashless transactions.
*   **🧾 Reporting & Invoicing:** Automatically generate comprehensive sales reports and export customized PDF invoices.
*   **🇰🇭 Fully Localized:** Complete Khmer language UI support with integrated custom Khmer fonts for staff ease of use.
*   **📱 Responsive Layout:** Clean, App-Shell architecture ensuring smooth scrolling and optimal display.

## 🛠️ Technology Stack

*   **Backend:** Python, Flask
*   **Frontend:** HTML5, CSS3, Vanilla JavaScript, Tailwind CSS (Customized)
*   **Database & Cloud:** Local Database / Firebase (Blaze Plan integration)
*   **Payment Gateway:** ABA PayWay API
*   **Version Control:** Git & GitHub

## ⚙️ Installation & Local Setup
2. Create and activate a virtual environment:

Bash
python -m venv .venv
# For Windows:
.venv\Scripts\activate
# For macOS/Linux:
source .venv/bin/activate
3. Install required dependencies:

Bash
pip install -r requirements.txt
4. Configure Environment Variables:
Copy the .env.template file to a new file named .env and fill in your secure credentials (e.g., Database URIs, ABA PayWay API keys).

5. Start the application:

Bash
python start_app.py
The server will start locally. Open your browser and navigate to http://127.0.0.1:5000/

👨‍💻 Developer
Ream Kesovanna

📄 License
This project is proprietary and intended solely for the internal use of NSP Cosmetic Store. Unauthorized copying, modification, or distribution is prohibited.

Follow these steps to get the development environment running on your local machine:

**1. Clone the repository:**
```bash
git clone [https://github.com/kesovanna/nspcosmeticpos.git](https://github.com/kesovanna/nspcosmeticpos.git)
cd nspcosmeticpos
