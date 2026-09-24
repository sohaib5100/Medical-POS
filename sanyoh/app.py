import os
import io
import csv
import json
import sqlite3
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, jsonify, session, Response
from werkzeug.utils import secure_filename

# --- FOOLPROOF PATH DETECTION FOR JINJA2 ---
# Yeh line automatically aapke project folder ka absolute path nikal legi
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates')
STATIC_DIR = os.path.join(BASE_DIR, 'static')

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)
app.secret_key = "sanyoh_secret_session_crypto_key"
DB_PATH = os.path.join(BASE_DIR, "warehouse.db")

UPLOAD_FOLDER = STATIC_DIR
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp','PNG', 'JPG', 'JPEG', 'GIF', 'WEBP'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

LOW_STOCK_THRESHOLD = 5
DEFAULT_TAX_PERCENT = 5.5

# Company / registration details shown on every printed invoice
COMPANY_INFO = {
    "name": "Sanyoh International",
    "address": "Office# 2, Flat # 8, Block# 19, Allah Dad Plaza, G-8 Markaz, Islamabad, Pakistan",
    "website": "www.sanyohinternational.com",
    "phone": "0514473555",
    "email": "sanyoh.int@gmail.com",
    "ntn": "3132375",
    "gst": "26-00-9800-010-28",
    "license_no": "LCI-ELI-01179"
}

CATALOG_SEED = [
    ("Clip Applier", "X-5x330ML", "MDIE-0001616"),
    ("Clip Applier", "X-10x330L", "MDIE-0001616"),
    ("Clip Applier", "X-10x330XL", "MDIE-0001616"),
    ("Disposable Ligation Clips", "JZJ-ML-6", "0008846"),
    ("Disposable Ligation Clips", "JZJ-L-6", "0008846"),
    ("Disposable Ligation Clips", "JZJ-XL-6", "0008846"),
    ("Disposable Trocar", "XY-CCQ-O05", "0008847"),
    ("Disposable Trocar", "XY-CCQ-O010", "0008847"),
    ("Disposable Trocar", "XY-CCQ-O12", "0008847"),
    ("Disposable Trocar", "XY-CCQ-O05/05/10/10", "0008847"),
    ("Disposable Core Biopsy Instrument", "BN-OCR-1/1610", "MDIR-0008848"),
    ("Disposable Core Biopsy Instrument", "BN-OCR-1/1616", "MDIR-0008848"),
    ("Disposable Core Biopsy Instrument", "BN-OCR-1/1810", "MDIR-0008848"),
    ("Disposable Core Biopsy Instrument", "BN-OCR-1/1816", "MDIR-0008848"),
    ("PN-OCR-1/1820", "MDIR-0008848", "MDIR-0008848"),
    ("Circumplast Circumcision Device", "CIRC-11, 11mm", "MDIR-0008787"),
    ("Circumplast Circumcision Device", "CIRC-12, 12mm", "MDIR-0008787"),
    ("Circumplast Circumcision Device", "CIRC-13, 13mm", "MDIR-0008787"),
    ("Circumplast Circumcision Device", "CIRC-95, 9.5mm", "MDIR-0008787"),
]

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def ensure_column(cursor, table, column, ddl):
    cursor.execute(f"PRAGMA table_info({table})")
    existing_cols = [col[1] for col in cursor.fetchall()]
    if column not in existing_cols:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")

def init_db():
    print("--> Starting database initialization...")
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            quantity INTEGER NOT NULL,
            image_name TEXT DEFAULT 'image_91e482.png',
            variant_size TEXT DEFAULT 'N/A',
            batch_number TEXT DEFAULT 'N/A',
            mfg_date TEXT DEFAULT 'N/A',
            expiry_date TEXT DEFAULT 'N/A',
            registration_number TEXT DEFAULT 'N/A'
        )
    ''')
    ensure_column(cursor, "products", "registration_number", "registration_number TEXT DEFAULT 'N/A'")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inventory_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            transaction_type TEXT NOT NULL,
            product_name TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            price_per_unit REAL NOT NULL,
            total_amount REAL NOT NULL
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS catalog_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL,
            model_size TEXT NOT NULL,
            registration_number TEXT NOT NULL
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_number TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            customer_name TEXT DEFAULT '',
            customer_phone TEXT DEFAULT '',
            customer_address TEXT DEFAULT '',
            gross_amount REAL DEFAULT 0,
            discount_amount REAL DEFAULT 0,
            sales_tax REAL DEFAULT 0,
            net_total REAL DEFAULT 0
        )
    ''')

    TEST_PRODUCT_NAMES = ["Premium Industrial Fitting A1", "High-Grade Core Coupler", "Trocar (10mm)"]
    cursor.executemany("DELETE FROM products WHERE name = ?", [(n,) for n in TEST_PRODUCT_NAMES])

    # One-time (repeat-safe) rename: the client's registered "XINWEL Clip Appliers"
    # catalog line is now just listed as "Clip Applier" - this keeps any database
    # that was already seeded with the old name in sync, without touching
    # historical inventory/transaction records tied to real stock already sold.
    cursor.execute(
        "UPDATE catalog_products SET product_name = 'Clip Applier' WHERE product_name = 'XINWEL Clip Appliers'"
    )

    cursor.execute("SELECT COUNT(*) FROM catalog_products")
    if cursor.fetchone()[0] == 0:
        cursor.executemany(
            "INSERT INTO catalog_products (product_name, model_size, registration_number) VALUES (?, ?, ?)",
            CATALOG_SEED
        )

    conn.commit()
    conn.close()
    print("--> Database tracking matrices fully operational.")

init_db()

def generate_invoice_number(cursor):
    year = datetime.now().year
    cursor.execute("SELECT COUNT(*) FROM invoices WHERE invoice_number LIKE ?", (f"SI-{year}-%",))
    seq = cursor.fetchone()[0] + 1
    return f"SI-{year}-{seq:04d}"

@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/dashboard')
def dashboard():
    # --- SECURITY CHECK ---
    if 'user' not in session:
        return redirect(url_for('login'))
    # ---------------------
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM products")
    all_products = [dict(row) for row in cursor.fetchall()]

    cursor.execute("SELECT * FROM inventory_transactions ORDER BY id DESC")
    all_transactions = [dict(row) for row in cursor.fetchall()]

    cursor.execute("SELECT product_name, model_size, registration_number FROM catalog_products ORDER BY product_name, model_size")
    catalog_rows = [dict(row) for row in cursor.fetchall()]

    conn.close()

    total_products = len(all_products)
    total_stock_units = sum(p['quantity'] for p in all_products)
    total_stock_value = sum(p['quantity'] * p['price'] for p in all_products)
    low_stock_items = [p for p in all_products if p['quantity'] <= LOW_STOCK_THRESHOLD]
    low_stock_count = len(low_stock_items)

    summary = {
        "total_products": total_products,
        "total_stock_units": total_stock_units,
        "total_stock_value": total_stock_value,
        "low_stock_count": low_stock_count,
        "low_stock_items": low_stock_items
    }

    return render_template(
        'dashboard.html',
        products=all_products,
        transactions=all_transactions,
        summary=summary,
        catalog_json=json.dumps(catalog_rows),
        error_message=request.args.get('error')
    )

@app.route('/inward-stock', methods=['POST'])
def inward_stock():
    try:
        action_type = request.form.get('action_type')
        conn = get_db_connection()
        cursor = conn.cursor()
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if action_type == 'existing':
            product_id = request.form.get('product_id')
            added_qty = int(request.form.get('quantity', 0))

            if added_qty <= 0:
                conn.close()
                return jsonify({"status": "error", "message": "Quantity must be greater than zero."}), 400

            cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
            prod = cursor.fetchone()
            if not prod:
                conn.close()
                return jsonify({"status": "error", "message": "Selected product could not be found."}), 404

            # Optional batch/mfg/expiry fields for this restock. A new batch of an
            # existing product is a different physical lot with its own expiry, so
            # it must never be silently merged into the old batch's quantity.
            new_batch = (request.form.get('batch_number') or '').strip()
            new_mfg = (request.form.get('mfg_date') or '').strip()
            new_exp = (request.form.get('expiry_date') or '').strip()

            existing_batch = prod['batch_number'] or 'N/A'

            batch_changed = bool(new_batch) and new_batch != existing_batch

            if batch_changed:
                # Different batch/lot: keep it as its own trackable stock line so the
                # correct batch/expiry is always shown at billing time, instead of
                # blending two lots together under one quantity number.
                cursor.execute('''
                    INSERT INTO products (name, price, quantity, image_name, variant_size, batch_number, mfg_date, expiry_date, registration_number)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    prod['name'], prod['price'], added_qty, prod['image_name'], prod['variant_size'],
                    new_batch, new_mfg or 'N/A', new_exp or 'N/A', prod['registration_number']
                ))
            else:
                cursor.execute("UPDATE products SET quantity = quantity + ? WHERE id = ?", (added_qty, product_id))

            total_cost = prod['price'] * added_qty
            cursor.execute('''
                INSERT INTO inventory_transactions (timestamp, transaction_type, product_name, quantity, price_per_unit, total_amount)
                VALUES (?, 'INWARD', ?, ?, ?, ?)
            ''', (current_time, prod['name'], added_qty, prod['price'], total_cost))
            conn.commit()
            conn.close()
            batch_note = f" (Batch: {new_batch})" if batch_changed else ""
            return jsonify({"status": "success", "message": f"Added {added_qty} units to {prod['name']}{batch_note}."})

        elif action_type == 'new':
            raw_name = (request.form.get('name') or '').strip()
            size = request.form.get('variant_size', 'N/A') or 'N/A'
            qty = int(request.form.get('quantity', 0) or 0)
            batch = request.form.get('batch_number', 'N/A') or 'N/A'
            mfg = request.form.get('mfg_date', 'N/A') or 'N/A'
            exp = request.form.get('expiry_date', 'N/A') or 'N/A'
            registration = request.form.get('registration_number', 'N/A') or 'N/A'

            if not raw_name:
                conn.close()
                return jsonify({"status": "error", "message": "Product name is required."}), 400
            try:
                price = float(request.form.get('price', 0.0) or 0)
            except ValueError:
                conn.close()
                return jsonify({"status": "error", "message": "Unit price must be a number."}), 400
            if price <= 0 or qty <= 0:
                conn.close()
                return jsonify({"status": "error", "message": "Price and quantity must be greater than zero."}), 400

            combined_name = f"{raw_name} ({size})" if size and size.lower() != 'n/a' else raw_name

            image_filename = 'image_91e482.png'
            if 'product_image' in request.files:
                file = request.files['product_image']
                if file and file.filename and allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    timestamp_str = datetime.now().strftime("%Y%m%d%H%M%S")
                    filename = f"prod_{timestamp_str}_{filename}"
                    file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                    image_filename = filename

            cursor.execute('''
                INSERT INTO products (name, price, quantity, image_name, variant_size, batch_number, mfg_date, expiry_date, registration_number)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (combined_name, price, qty, image_filename, size, batch, mfg, exp, registration))

            total_cost = price * qty
            cursor.execute('''
                INSERT INTO inventory_transactions (timestamp, transaction_type, product_name, quantity, price_per_unit, total_amount)
                VALUES (?, 'INWARD', ?, ?, ?, ?)
            ''', (current_time, combined_name, qty, price, total_cost))
            conn.commit()
            conn.close()
            return jsonify({"status": "success", "message": f"{combined_name} added to inventory."})

        conn.close()
        return jsonify({"status": "error", "message": "Unrecognized form action."}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": f"Could not save stock entry: {str(e)}"}), 500

@app.route('/delete-product/<int:product_id>', methods=['POST'])
def delete_product(product_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM products WHERE id = ?", (product_id,))
        prod = cursor.fetchone()
        if not prod:
            conn.close()
            return jsonify({"status": "error", "message": "That product no longer exists."}), 404
        cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": f"{prod['name']} was deleted."})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Could not delete product: {str(e)}"}), 500

@app.route('/sales-analytics-data')
def sales_analytics_data():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT timestamp, total_amount, transaction_type FROM inventory_transactions")
    rows = cursor.fetchall()
    conn.close()

    current_year = datetime.now().year
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    monthly_sales = [0.0] * 12
    monthly_inward = [0.0] * 12

    for row in rows:
        try:
            date_obj = datetime.strptime(row['timestamp'].split()[0], "%Y-%m-%d")
            if date_obj.year == current_year:
                month_idx = date_obj.month - 1
                if row['transaction_type'] == 'OUTWARD':
                    monthly_sales[month_idx] += row['total_amount']
                elif row['transaction_type'] == 'INWARD':
                    monthly_inward[month_idx] += row['total_amount']
        except Exception:
            continue

    yearly_total_revenue = sum(monthly_sales)
    yearly_total_inward = sum(monthly_inward)

    return jsonify({
        "labels": months, "sales": monthly_sales, "inward": monthly_inward,
        "yearly_total_revenue": yearly_total_revenue, "yearly_total_inward": yearly_total_inward,
        "yearly_gross_margin": yearly_total_revenue - yearly_total_inward, "year": current_year
    })

@app.route('/sales-report-data')
def sales_report_data():
    start = request.args.get('start')
    end = request.args.get('end')

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT timestamp, transaction_type, product_name, quantity, total_amount FROM inventory_transactions ORDER BY timestamp ASC")
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    def in_range(ts):
        try:
            d = datetime.strptime(ts.split()[0], "%Y-%m-%d").date()
        except Exception:
            return False
        if start and d < datetime.strptime(start, "%Y-%m-%d").date(): return False
        if end and d > datetime.strptime(end, "%Y-%m-%d").date(): return False
        return True

    filtered = [r for r in rows if in_range(r['timestamp'])]
    total_sales = sum(r['total_amount'] for r in filtered if r['transaction_type'] == 'OUTWARD')
    total_purchases = sum(r['total_amount'] for r in filtered if r['transaction_type'] == 'INWARD')
    sale_rows = [r for r in filtered if r['transaction_type'] == 'OUTWARD']
    transaction_count = len(sale_rows)
    average_sale = (total_sales / transaction_count) if transaction_count else 0.0

    product_stats = {}
    for r in sale_rows:
        p = product_stats.setdefault(r['product_name'], {"revenue": 0.0, "qty": 0})
        p["revenue"] += r['total_amount']
        p["qty"] += r['quantity']

    top_by_revenue = sorted(
        [{"name": k, "revenue": v["revenue"], "qty": v["qty"]} for k, v in product_stats.items()],
        key=lambda x: x["revenue"], reverse=True
    )[:5]

    daily = {}
    for r in filtered:
        day = r['timestamp'].split()[0]
        bucket = daily.setdefault(day, {"sales": 0.0, "purchases": 0.0})
        if r['transaction_type'] == 'OUTWARD': bucket["sales"] += r['total_amount']
        else: bucket["purchases"] += r['total_amount']
    daily_sorted = sorted(daily.items(), key=lambda x: x[0])

    return jsonify({
        "start": start, "end": end, "total_sales": total_sales, "total_purchases": total_purchases,
        "gross_margin": total_sales - total_purchases, "transaction_count": transaction_count,
        "average_sale": average_sale, "top_products": top_by_revenue,
        "daily_labels": [d for d, _ in daily_sorted], "daily_sales": [v["sales"] for _, v in daily_sorted],
        "daily_purchases": [v["purchases"] for _, v in daily_sorted]
    })

@app.route('/export-sales-report-csv')
def export_sales_report_csv():
    start = request.args.get('start')
    end = request.args.get('end')

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT timestamp, transaction_type, product_name, quantity, price_per_unit, total_amount FROM inventory_transactions ORDER BY timestamp ASC")
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    def in_range(ts):
        try:
            d = datetime.strptime(ts.split()[0], "%Y-%m-%d").date()
        except Exception:
            return False
        if start and d < datetime.strptime(start, "%Y-%m-%d").date(): return False
        if end and d > datetime.strptime(end, "%Y-%m-%d").date(): return False
        return True

    filtered = [r for r in rows if in_range(r['timestamp'])]
    total_sales = sum(r['total_amount'] for r in filtered if r['transaction_type'] == 'OUTWARD')
    total_purchases = sum(r['total_amount'] for r in filtered if r['transaction_type'] == 'INWARD')

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([f"Sanyoh International - Sales & Inventory Report"])
    writer.writerow([f"Period: {start or 'All time'} to {end or 'Present'}"])
    writer.writerow([])
    writer.writerow(["Date & Time", "Type", "Product", "Quantity", "Unit Price", "Total Amount"])
    for r in filtered:
        writer.writerow([r['timestamp'], r['transaction_type'], r['product_name'], r['quantity'], r['price_per_unit'], r['total_amount']])
    writer.writerow([])
    writer.writerow(["Total Sales", "", "", "", "", round(total_sales, 2)])
    writer.writerow(["Total Purchases", "", "", "", "", round(total_purchases, 2)])
    writer.writerow(["Gross Margin", "", "", "", "", round(total_sales - total_purchases, 2)])

    filename = f"sales_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"})

@app.route('/print-transaction/<int:transaction_id>')
def print_transaction(transaction_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM inventory_transactions WHERE id = ?", (transaction_id,))
    tx = cursor.fetchone()
    conn.close()
    if not tx: return "Transaction not found.", 404
    return render_template('print_transaction.html', tx=dict(tx), company=COMPANY_INFO)

@app.route('/export-inventory-csv')
def export_inventory_csv():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM products")
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Product Name", "Registration #", "Batch Number", "Unit Price", "Quantity", "Stock Value", "Mfg Date", "Expiry Date"])
    for p in rows:
        writer.writerow([
            p['name'], p.get('registration_number', 'N/A'), p['batch_number'], p['price'], p['quantity'],
            round(p['price'] * p['quantity'], 2), p['mfg_date'], p['expiry_date']
        ])

    filename = f"inventory_report_{datetime.now().strftime('%Y%m%d')}.csv"
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"})

@app.route('/bill')
def bill():
    # ---securing the connection
    if 'user' not in session:
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, price, quantity, batch_number, mfg_date, expiry_date, registration_number FROM products")
    all_items = cursor.fetchall()
    next_invoice_number = generate_invoice_number(cursor)
    conn.close()
    return render_template(
        'bill.html', items=all_items, company=COMPANY_INFO,
        default_tax_percent=DEFAULT_TAX_PERCENT, next_invoice_number=next_invoice_number
    )

@app.route('/checkout', methods=['POST'])
def checkout():
    try:
        data = request.get_json()
        cart = data.get('cart', [])
        sales_tax = float(data.get('sales_tax', 0) or 0)
        customer = data.get('customer', {}) or {}

        conn = get_db_connection()
        cursor = conn.cursor()
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        gross_amount_total = 0.0
        discount_amount_total = 0.0

        for item in cart:
            # Sell from the exact batch/lot the cashier picked at the billing
            # screen (matched by product row id), never just "any row with this
            # product name" - otherwise stock could be deducted from the wrong
            # batch and the wrong expiry date would end up on the invoice.
            product_id = item.get('id')
            prod_row = None
            if product_id:
                cursor.execute("SELECT name, price FROM products WHERE id = ?", (product_id,))
                prod_row = cursor.fetchone()
            if not prod_row:
                # Fallback for any older cart payload that only sends a name
                cursor.execute("SELECT name, price FROM products WHERE name = ?", (item.get('name'),))
                prod_row = cursor.fetchone()
            price_val = prod_row['price'] if prod_row else 0.0
            item_name = prod_row['name'] if prod_row else item.get('name', '')

            discount_percent = float(item.get('discount_percent', 0) or 0)
            gross_amount = price_val * int(item['qty'])
            line_discount = gross_amount * (discount_percent / 100)
            total_sales_amount = gross_amount - line_discount

            gross_amount_total += gross_amount
            discount_amount_total += line_discount

            if product_id:
                cursor.execute("UPDATE products SET quantity = MAX(0, quantity - ?) WHERE id = ?", (item['qty'], product_id))
            else:
                cursor.execute("UPDATE products SET quantity = MAX(0, quantity - ?) WHERE name = ?", (item['qty'], item_name))
            cursor.execute('''
                INSERT INTO inventory_transactions (timestamp, transaction_type, product_name, quantity, price_per_unit, total_amount)
                VALUES (?, 'OUTWARD', ?, ?, ?, ?)
            ''', (current_time, item_name, item['qty'], price_val, total_sales_amount))

        net_total = (gross_amount_total - discount_amount_total) + sales_tax
        invoice_number = generate_invoice_number(cursor)
        cursor.execute('''
            INSERT INTO invoices (invoice_number, timestamp, customer_name, customer_phone, customer_address, gross_amount, discount_amount, sales_tax, net_total)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            invoice_number, current_time, customer.get('name', ''), customer.get('phone', ''), customer.get('address', ''),
            gross_amount_total, discount_amount_total, sales_tax, net_total
        ))
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "invoice_number": invoice_number})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        session['user'] = request.form.get('email')
        return redirect(url_for('dashboard'))
    return render_template('login.html', error=None)

@app.route('/logout')
def logout():
    session.clear()
    return render_template("logout.html")

if __name__ == '__main__':
    app.run(debug=True, port=5000)












''''3'''
# import os
# import io
# import csv
# import json
# import sqlite3
# from datetime import datetime
# from flask import Flask, render_template, request, redirect, url_for, jsonify, session, Response
# from werkzeug.utils import secure_filename

# # --- FOOLPROOF PATH DETECTION FOR JINJA2 ---
# # Yeh line automatically aapke project folder ka absolute path nikal legi
# BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates')
# STATIC_DIR = os.path.join(BASE_DIR, 'static')

# app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)
# app.secret_key = "sanyoh_secret_session_crypto_key"
# DB_PATH = os.path.join(BASE_DIR, "warehouse.db")

# UPLOAD_FOLDER = STATIC_DIR
# ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp','PNG', 'JPG', 'JPEG', 'GIF', 'WEBP'}
# app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# LOW_STOCK_THRESHOLD = 5
# DEFAULT_TAX_PERCENT = 5.5

# # Company / registration details shown on every printed invoice
# COMPANY_INFO = {
#     "name": "Sanyoh International",
#     "address": "Office# 2, Flat # 8, Block# 19, Allah Dad Plaza, G-8 Markaz, Islamabad, Pakistan",
#     "website": "www.sanyohinternational.com",
#     "phone": "0514473555",
#     "ntn": "3132375",
#     "gst": "26-00-9800-010-28"
# }

# CATALOG_SEED = [
#     ("XINWEL Clip Appliers", "X-5x330ML", "MDIE-0001616"),
#     ("XINWEL Clip Appliers", "X-10x330L", "MDIE-0001616"),
#     ("XINWEL Clip Appliers", "X-10x330XL", "MDIE-0001616"),
#     ("Disposable Ligation Clips", "JZJ-ML-6", "0008846"),
#     ("Disposable Ligation Clips", "JZJ-L-6", "0008846"),
#     ("Disposable Ligation Clips", "JZJ-XL-6", "0008846"),
#     ("Disposable Trocar", "XY-CCQ-O05", "0008847"),
#     ("Disposable Trocar", "XY-CCQ-O010", "0008847"),
#     ("Disposable Trocar", "XY-CCQ-O12", "0008847"),
#     ("Disposable Trocar", "XY-CCQ-O05/05/10/10", "0008847"),
#     ("Disposable Core Biopsy Instrument", "BN-OCR-1/1610", "MDIR-0008848"),
#     ("Disposable Core Biopsy Instrument", "BN-OCR-1/1616", "MDIR-0008848"),
#     ("Disposable Core Biopsy Instrument", "BN-OCR-1/1810", "MDIR-0008848"),
#     ("Disposable Core Biopsy Instrument", "BN-OCR-1/1816", "MDIR-0008848"),
#     ("PN-OCR-1/1820", "MDIR-0008848", "MDIR-0008848"),
#     ("Circumplast Circumcision Device", "CIRC-11, 11mm", "MDIR-0008787"),
#     ("Circumplast Circumcision Device", "CIRC-12, 12mm", "MDIR-0008787"),
#     ("Circumplast Circumcision Device", "CIRC-13, 13mm", "MDIR-0008787"),
#     ("Circumplast Circumcision Device", "CIRC-95, 9.5mm", "MDIR-0008787"),
# ]

# def allowed_file(filename):
#     return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# def get_db_connection():
#     conn = sqlite3.connect(DB_PATH)
#     conn.row_factory = sqlite3.Row  
#     return conn

# def ensure_column(cursor, table, column, ddl):
#     cursor.execute(f"PRAGMA table_info({table})")
#     existing_cols = [col[1] for col in cursor.fetchall()]
#     if column not in existing_cols:
#         cursor.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")

# def init_db():
#     print("--> Starting database initialization...")
#     conn = get_db_connection()
#     cursor = conn.cursor()

#     cursor.execute('''
#         CREATE TABLE IF NOT EXISTS products (
#             id INTEGER PRIMARY KEY AUTOINCREMENT,
#             name TEXT NOT NULL,
#             price REAL NOT NULL,
#             quantity INTEGER NOT NULL,
#             image_name TEXT DEFAULT 'image_91e482.png',
#             variant_size TEXT DEFAULT 'N/A',
#             batch_number TEXT DEFAULT 'N/A',
#             mfg_date TEXT DEFAULT 'N/A',
#             expiry_date TEXT DEFAULT 'N/A',
#             registration_number TEXT DEFAULT 'N/A'
#         )
#     ''')
#     ensure_column(cursor, "products", "registration_number", "registration_number TEXT DEFAULT 'N/A'")

#     cursor.execute('''
#         CREATE TABLE IF NOT EXISTS inventory_transactions (
#             id INTEGER PRIMARY KEY AUTOINCREMENT,
#             timestamp TEXT NOT NULL,
#             transaction_type TEXT NOT NULL,
#             product_name TEXT NOT NULL,
#             quantity INTEGER NOT NULL,
#             price_per_unit REAL NOT NULL,
#             total_amount REAL NOT NULL
#         )
#     ''')

#     cursor.execute('''
#         CREATE TABLE IF NOT EXISTS catalog_products (
#             id INTEGER PRIMARY KEY AUTOINCREMENT,
#             product_name TEXT NOT NULL,
#             model_size TEXT NOT NULL,
#             registration_number TEXT NOT NULL
#         )
#     ''')

#     cursor.execute('''
#         CREATE TABLE IF NOT EXISTS invoices (
#             id INTEGER PRIMARY KEY AUTOINCREMENT,
#             invoice_number TEXT NOT NULL,
#             timestamp TEXT NOT NULL,
#             customer_name TEXT DEFAULT '',
#             customer_phone TEXT DEFAULT '',
#             customer_address TEXT DEFAULT '',
#             gross_amount REAL DEFAULT 0,
#             discount_amount REAL DEFAULT 0,
#             sales_tax REAL DEFAULT 0,
#             net_total REAL DEFAULT 0
#         )
#     ''')
    
#     TEST_PRODUCT_NAMES = ["Premium Industrial Fitting A1", "High-Grade Core Coupler", "Trocar (10mm)"]
#     cursor.executemany("DELETE FROM products WHERE name = ?", [(n,) for n in TEST_PRODUCT_NAMES])

#     cursor.execute("SELECT COUNT(*) FROM catalog_products")
#     if cursor.fetchone()[0] == 0:
#         cursor.executemany(
#             "INSERT INTO catalog_products (product_name, model_size, registration_number) VALUES (?, ?, ?)",
#             CATALOG_SEED
#         )
    
#     conn.commit()
#     conn.close()
#     print("--> Database tracking matrices fully operational.")

# init_db()

# def generate_invoice_number(cursor):
#     year = datetime.now().year
#     cursor.execute("SELECT COUNT(*) FROM invoices WHERE invoice_number LIKE ?", (f"SI-{year}-%",))
#     seq = cursor.fetchone()[0] + 1
#     return f"SI-{year}-{seq:04d}"

# @app.route('/')
# def index():
#     return redirect(url_for('dashboard'))

# @app.route('/dashboard')
# def dashboard():
#     conn = get_db_connection()
#     cursor = conn.cursor()
    
#     cursor.execute("SELECT * FROM products")
#     all_products = [dict(row) for row in cursor.fetchall()]
    
#     cursor.execute("SELECT * FROM inventory_transactions ORDER BY id DESC")
#     all_transactions = [dict(row) for row in cursor.fetchall()]

#     cursor.execute("SELECT product_name, model_size, registration_number FROM catalog_products ORDER BY product_name, model_size")
#     catalog_rows = [dict(row) for row in cursor.fetchall()]
    
#     conn.close()

#     total_products = len(all_products)
#     total_stock_units = sum(p['quantity'] for p in all_products)
#     total_stock_value = sum(p['quantity'] * p['price'] for p in all_products)
#     low_stock_items = [p for p in all_products if p['quantity'] <= LOW_STOCK_THRESHOLD]
#     low_stock_count = len(low_stock_items)

#     summary = {
#         "total_products": total_products,
#         "total_stock_units": total_stock_units,
#         "total_stock_value": total_stock_value,
#         "low_stock_count": low_stock_count,
#         "low_stock_items": low_stock_items
#     }

#     return render_template(
#         'dashboard.html',
#         products=all_products,
#         transactions=all_transactions,
#         summary=summary,
#         catalog_json=json.dumps(catalog_rows),
#         error_message=request.args.get('error')
#     )

# @app.route('/inward-stock', methods=['POST'])
# def inward_stock():
#     try:
#         action_type = request.form.get('action_type')
#         conn = get_db_connection()
#         cursor = conn.cursor()
#         current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
#         if action_type == 'existing':
#             product_id = request.form.get('product_id')
#             added_qty = int(request.form.get('quantity', 0))

#             if added_qty <= 0:
#                 conn.close()
#                 return jsonify({"status": "error", "message": "Quantity must be greater than zero."}), 400
            
#             cursor.execute("SELECT name, price FROM products WHERE id = ?", (product_id,))
#             prod = cursor.fetchone()
#             if prod:
#                 cursor.execute("UPDATE products SET quantity = quantity + ? WHERE id = ?", (added_qty, product_id))
#                 total_cost = prod['price'] * added_qty
#                 cursor.execute('''
#                     INSERT INTO inventory_transactions (timestamp, transaction_type, product_name, quantity, price_per_unit, total_amount)
#                     VALUES (?, 'INWARD', ?, ?, ?, ?)
#                 ''', (current_time, prod['name'], added_qty, prod['price'], total_cost))
#                 conn.commit()
#                 conn.close()
#                 return jsonify({"status": "success", "message": f"Added {added_qty} units to {prod['name']}."})
#             else:
#                 conn.close()
#                 return jsonify({"status": "error", "message": "Selected product could not be found."}), 404
            
#         elif action_type == 'new':
#             raw_name = (request.form.get('name') or '').strip()
#             size = request.form.get('variant_size', 'N/A') or 'N/A'
#             qty = int(request.form.get('quantity', 0) or 0)
#             batch = request.form.get('batch_number', 'N/A') or 'N/A'
#             mfg = request.form.get('mfg_date', 'N/A') or 'N/A'
#             exp = request.form.get('expiry_date', 'N/A') or 'N/A'
#             registration = request.form.get('registration_number', 'N/A') or 'N/A'

#             if not raw_name:
#                 conn.close()
#                 return jsonify({"status": "error", "message": "Product name is required."}), 400
#             try:
#                 price = float(request.form.get('price', 0.0) or 0)
#             except ValueError:
#                 conn.close()
#                 return jsonify({"status": "error", "message": "Unit price must be a number."}), 400
#             if price <= 0 or qty <= 0:
#                 conn.close()
#                 return jsonify({"status": "error", "message": "Price and quantity must be greater than zero."}), 400
            
#             combined_name = f"{raw_name} ({size})" if size and size.lower() != 'n/a' else raw_name
            
#             image_filename = 'image_91e482.png'
#             if 'product_image' in request.files:
#                 file = request.files['product_image']
#                 if file and file.filename and allowed_file(file.filename):
#                     filename = secure_filename(file.filename)
#                     timestamp_str = datetime.now().strftime("%Y%m%d%H%M%S")
#                     filename = f"prod_{timestamp_str}_{filename}"
#                     file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
#                     image_filename = filename

#             cursor.execute('''
#                 INSERT INTO products (name, price, quantity, image_name, variant_size, batch_number, mfg_date, expiry_date, registration_number)
#                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
#             ''', (combined_name, price, qty, image_filename, size, batch, mfg, exp, registration))
            
#             total_cost = price * qty
#             cursor.execute('''
#                 INSERT INTO inventory_transactions (timestamp, transaction_type, product_name, quantity, price_per_unit, total_amount)
#                 VALUES (?, 'INWARD', ?, ?, ?, ?)
#             ''', (current_time, combined_name, qty, price, total_cost))
#             conn.commit()
#             conn.close()
#             return jsonify({"status": "success", "message": f"{combined_name} added to inventory."})

#         conn.close()
#         return jsonify({"status": "error", "message": "Unrecognized form action."}), 400
#     except Exception as e:
#         return jsonify({"status": "error", "message": f"Could not save stock entry: {str(e)}"}), 500

# @app.route('/delete-product/<int:product_id>', methods=['POST'])
# def delete_product(product_id):
#     try:
#         conn = get_db_connection()
#         cursor = conn.cursor()
#         cursor.execute("SELECT name FROM products WHERE id = ?", (product_id,))
#         prod = cursor.fetchone()
#         if not prod:
#             conn.close()
#             return jsonify({"status": "error", "message": "That product no longer exists."}), 404
#         cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
#         conn.commit()
#         conn.close()
#         return jsonify({"status": "success", "message": f"{prod['name']} was deleted."})
#     except Exception as e:
#         return jsonify({"status": "error", "message": f"Could not delete product: {str(e)}"}), 500

# @app.route('/sales-analytics-data')
# def sales_analytics_data():
#     conn = get_db_connection()
#     cursor = conn.cursor()
#     cursor.execute("SELECT timestamp, total_amount, transaction_type FROM inventory_transactions")
#     rows = cursor.fetchall()
#     conn.close()

#     current_year = datetime.now().year
#     months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
#     monthly_sales = [0.0] * 12
#     monthly_inward = [0.0] * 12

#     for row in rows:
#         try:
#             date_obj = datetime.strptime(row['timestamp'].split()[0], "%Y-%m-%d")
#             if date_obj.year == current_year:
#                 month_idx = date_obj.month - 1
#                 if row['transaction_type'] == 'OUTWARD':
#                     monthly_sales[month_idx] += row['total_amount']
#                 elif row['transaction_type'] == 'INWARD':
#                     monthly_inward[month_idx] += row['total_amount']
#         except Exception:
#             continue

#     yearly_total_revenue = sum(monthly_sales)
#     yearly_total_inward = sum(monthly_inward)

#     return jsonify({
#         "labels": months, "sales": monthly_sales, "inward": monthly_inward,
#         "yearly_total_revenue": yearly_total_revenue, "yearly_total_inward": yearly_total_inward,
#         "yearly_gross_margin": yearly_total_revenue - yearly_total_inward, "year": current_year
#     })

# @app.route('/sales-report-data')
# def sales_report_data():
#     start = request.args.get('start')
#     end = request.args.get('end')

#     conn = get_db_connection()
#     cursor = conn.cursor()
#     cursor.execute("SELECT timestamp, transaction_type, product_name, quantity, total_amount FROM inventory_transactions ORDER BY timestamp ASC")
#     rows = [dict(row) for row in cursor.fetchall()]
#     conn.close()

#     def in_range(ts):
#         try:
#             d = datetime.strptime(ts.split()[0], "%Y-%m-%d").date()
#         except Exception:
#             return False
#         if start and d < datetime.strptime(start, "%Y-%m-%d").date(): return False
#         if end and d > datetime.strptime(end, "%Y-%m-%d").date(): return False
#         return True

#     filtered = [r for r in rows if in_range(r['timestamp'])]
#     total_sales = sum(r['total_amount'] for r in filtered if r['transaction_type'] == 'OUTWARD')
#     total_purchases = sum(r['total_amount'] for r in filtered if r['transaction_type'] == 'INWARD')
#     sale_rows = [r for r in filtered if r['transaction_type'] == 'OUTWARD']
#     transaction_count = len(sale_rows)
#     average_sale = (total_sales / transaction_count) if transaction_count else 0.0

#     product_stats = {}
#     for r in sale_rows:
#         p = product_stats.setdefault(r['product_name'], {"revenue": 0.0, "qty": 0})
#         p["revenue"] += r['total_amount']
#         p["qty"] += r['quantity']

#     top_by_revenue = sorted(
#         [{"name": k, "revenue": v["revenue"], "qty": v["qty"]} for k, v in product_stats.items()],
#         key=lambda x: x["revenue"], reverse=True
#     )[:5]

#     daily = {}
#     for r in filtered:
#         day = r['timestamp'].split()[0]
#         bucket = daily.setdefault(day, {"sales": 0.0, "purchases": 0.0})
#         if r['transaction_type'] == 'OUTWARD': bucket["sales"] += r['total_amount']
#         else: bucket["purchases"] += r['total_amount']
#     daily_sorted = sorted(daily.items(), key=lambda x: x[0])

#     return jsonify({
#         "start": start, "end": end, "total_sales": total_sales, "total_purchases": total_purchases,
#         "gross_margin": total_sales - total_purchases, "transaction_count": transaction_count,
#         "average_sale": average_sale, "top_products": top_by_revenue,
#         "daily_labels": [d for d, _ in daily_sorted], "daily_sales": [v["sales"] for _, v in daily_sorted],
#         "daily_purchases": [v["purchases"] for _, v in daily_sorted]
#     })

# @app.route('/export-sales-report-csv')
# def export_sales_report_csv():
#     start = request.args.get('start')
#     end = request.args.get('end')

#     conn = get_db_connection()
#     cursor = conn.cursor()
#     cursor.execute("SELECT timestamp, transaction_type, product_name, quantity, price_per_unit, total_amount FROM inventory_transactions ORDER BY timestamp ASC")
#     rows = [dict(row) for row in cursor.fetchall()]
#     conn.close()

#     def in_range(ts):
#         try:
#             d = datetime.strptime(ts.split()[0], "%Y-%m-%d").date()
#         except Exception:
#             return False
#         if start and d < datetime.strptime(start, "%Y-%m-%d").date(): return False
#         if end and d > datetime.strptime(end, "%Y-%m-%d").date(): return False
#         return True

#     filtered = [r for r in rows if in_range(r['timestamp'])]
#     total_sales = sum(r['total_amount'] for r in filtered if r['transaction_type'] == 'OUTWARD')
#     total_purchases = sum(r['total_amount'] for r in filtered if r['transaction_type'] == 'INWARD')

#     output = io.StringIO()
#     writer = csv.writer(output)
#     writer.writerow([f"Sanyoh International - Sales & Inventory Report"])
#     writer.writerow([f"Period: {start or 'All time'} to {end or 'Present'}"])
#     writer.writerow([])
#     writer.writerow(["Date & Time", "Type", "Product", "Quantity", "Unit Price", "Total Amount"])
#     for r in filtered:
#         writer.writerow([r['timestamp'], r['transaction_type'], r['product_name'], r['quantity'], r['price_per_unit'], r['total_amount']])
#     writer.writerow([])
#     writer.writerow(["Total Sales", "", "", "", "", round(total_sales, 2)])
#     writer.writerow(["Total Purchases", "", "", "", "", round(total_purchases, 2)])
#     writer.writerow(["Gross Margin", "", "", "", "", round(total_sales - total_purchases, 2)])

#     filename = f"sales_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
#     return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"})

# @app.route('/print-transaction/<int:transaction_id>')
# def print_transaction(transaction_id):
#     conn = get_db_connection()
#     cursor = conn.cursor()
#     cursor.execute("SELECT * FROM inventory_transactions WHERE id = ?", (transaction_id,))
#     tx = cursor.fetchone()
#     conn.close()
#     if not tx: return "Transaction not found.", 404
#     return render_template('print_transaction.html', tx=dict(tx), company=COMPANY_INFO)

# @app.route('/export-inventory-csv')
# def export_inventory_csv():
#     conn = get_db_connection()
#     cursor = conn.cursor()
#     cursor.execute("SELECT * FROM products")
#     rows = [dict(row) for row in cursor.fetchall()]
#     conn.close()

#     output = io.StringIO()
#     writer = csv.writer(output)
#     writer.writerow(["Product Name", "Registration #", "Batch Number", "Unit Price", "Quantity", "Stock Value", "Mfg Date", "Expiry Date"])
#     for p in rows:
#         writer.writerow([
#             p['name'], p.get('registration_number', 'N/A'), p['batch_number'], p['price'], p['quantity'],
#             round(p['price'] * p['quantity'], 2), p['mfg_date'], p['expiry_date']
#         ])

#     filename = f"inventory_report_{datetime.now().strftime('%Y%m%d')}.csv"
#     return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"})

# @app.route('/bill')
# def bill():
#     conn = get_db_connection()
#     cursor = conn.cursor()
#     cursor.execute("SELECT name, price, quantity, batch_number, mfg_date, expiry_date, registration_number FROM products")
#     all_items = cursor.fetchall()
#     next_invoice_number = generate_invoice_number(cursor)
#     conn.close()
#     return render_template(
#         'bill.html', items=all_items, company=COMPANY_INFO,
#         default_tax_percent=DEFAULT_TAX_PERCENT, next_invoice_number=next_invoice_number
#     )

# @app.route('/checkout', methods=['POST'])
# def checkout():
#     try:
#         data = request.get_json()
#         cart = data.get('cart', [])
#         sales_tax = float(data.get('sales_tax', 0) or 0)
#         customer = data.get('customer', {}) or {}

#         conn = get_db_connection()
#         cursor = conn.cursor()
#         current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

#         gross_amount_total = 0.0
#         discount_amount_total = 0.0
        
#         for item in cart:
#             cursor.execute("SELECT price FROM products WHERE name = ?", (item['name'],))
#             prod_row = cursor.fetchone()
#             price_val = prod_row['price'] if prod_row else 0.0

#             discount_percent = float(item.get('discount_percent', 0) or 0)
#             gross_amount = price_val * int(item['qty'])
#             line_discount = gross_amount * (discount_percent / 100)
#             total_sales_amount = gross_amount - line_discount

#             gross_amount_total += gross_amount
#             discount_amount_total += line_discount
            
#             cursor.execute("UPDATE products SET quantity = MAX(0, quantity - ?) WHERE name = ?", (item['qty'], item['name']))
#             cursor.execute('''
#                 INSERT INTO inventory_transactions (timestamp, transaction_type, product_name, quantity, price_per_unit, total_amount)
#                 VALUES (?, 'OUTWARD', ?, ?, ?, ?)
#             ''', (current_time, item['name'], item['qty'], price_val, total_sales_amount))

#         net_total = (gross_amount_total - discount_amount_total) + sales_tax
#         invoice_number = generate_invoice_number(cursor)
#         cursor.execute('''
#             INSERT INTO invoices (invoice_number, timestamp, customer_name, customer_phone, customer_address, gross_amount, discount_amount, sales_tax, net_total)
#             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
#         ''', (
#             invoice_number, current_time, customer.get('name', ''), customer.get('phone', ''), customer.get('address', ''),
#             gross_amount_total, discount_amount_total, sales_tax, net_total
#         ))
#         conn.commit()
#         conn.close()
#         return jsonify({"status": "success", "invoice_number": invoice_number})
#     except Exception as e:
#         return jsonify({"status": "error", "message": str(e)}), 500

# @app.route('/login', methods=['GET', 'POST'])
# def login():
#     if request.method == 'POST':
#         session['user'] = request.form.get('email')
#         return redirect(url_for('dashboard'))
#     return render_template('login.html', error=None)

# @app.route('/logout')
# def logout():
#     session.clear()
#     return render_template("logout.html")

# if __name__ == '__main__':
#     app.run(debug=True, port=5000)












''' 2'''
# import os
# import io
# import csv
# import json
# import sqlite3
# from datetime import datetime
# from flask import Flask, render_template, request, redirect, url_for, jsonify, session, Response
# from werkzeug.utils import secure_filename

# app = Flask(__name__)
# app.secret_key = "sanyoh_secret_session_crypto_key"
# DB_PATH = "warehouse.db"

# UPLOAD_FOLDER = os.path.join('static')
# ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp','PNG', 'JPG', 'JPEG', 'GIF', 'WEBP'}
# app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# LOW_STOCK_THRESHOLD = 5
# DEFAULT_TAX_PERCENT = 5.5

# # Company / registration details shown on every printed invoice
# COMPANY_INFO = {
#     "name": "Sanyoh International",
#     "address": "Office# 2, Flat # 8, Block# 19, Allah Dad Plaza, G-8 Markaz, Islamabad, Pakistan",
#     "website": "www.sanyohinternational.com",
#     "phone": "0514473555",
#     "ntn": "3132375",
#     "gst": "26-00-9800-010-28"
# }

# # Seed catalog of registered products supplied by the client. Each model/size
# # variant is its own catalog row so it can be searched and picked directly
# # when adding new stock, instead of typing product details by hand.
# CATALOG_SEED = [
#     ("XINWEL Clip Appliers", "X-5x330ML", "MDIE-0001616"),
#     ("XINWEL Clip Appliers", "X-10x330L", "MDIE-0001616"),
#     ("XINWEL Clip Appliers", "X-10x330XL", "MDIE-0001616"),

#     ("Disposable Ligation Clips", "JZJ-ML-6", "0008846"),
#     ("Disposable Ligation Clips", "JZJ-L-6", "0008846"),
#     ("Disposable Ligation Clips", "JZJ-XL-6", "0008846"),

#     ("Disposable Trocar", "XY-CCQ-O05", "0008847"),
#     ("Disposable Trocar", "XY-CCQ-O010", "0008847"),
#     ("Disposable Trocar", "XY-CCQ-O12", "0008847"),
#     ("Disposable Trocar", "XY-CCQ-O05/05/10/10", "0008847"),

#     ("Disposable Core Biopsy Instrument", "BN-OCR-1/1610", "MDIR-0008848"),
#     ("Disposable Core Biopsy Instrument", "BN-OCR-1/1616", "MDIR-0008848"),
#     ("Disposable Core Biopsy Instrument", "BN-OCR-1/1810", "MDIR-0008848"),
#     ("Disposable Core Biopsy Instrument", "BN-OCR-1/1816", "MDIR-0008848"),
#     ("Disposable Core Biopsy Instrument", "PN-OCR-1/1820", "MDIR-0008848"),

#     ("Circumplast Circumcision Device", "CIRC-11, 11mm", "MDIR-0008787"),
#     ("Circumplast Circumcision Device", "CIRC-12, 12mm", "MDIR-0008787"),
#     ("Circumplast Circumcision Device", "CIRC-13, 13mm", "MDIR-0008787"),
#     ("Circumplast Circumcision Device", "CIRC-95, 9.5mm", "MDIR-0008787"),
# ]

# def allowed_file(filename):
#     return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# def get_db_connection():
#     conn = sqlite3.connect(DB_PATH)
#     conn.row_factory = sqlite3.Row  
#     return conn

# def ensure_column(cursor, table, column, ddl):
#     """Additive migration helper: adds a column only if it's missing,
#     never drops or rebuilds existing tables/data."""
#     cursor.execute(f"PRAGMA table_info({table})")
#     existing_cols = [col[1] for col in cursor.fetchall()]
#     if column not in existing_cols:
#         cursor.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")

# def init_db():
#     print("--> Starting database initialization...")
#     conn = get_db_connection()
#     cursor = conn.cursor()

#     # Master Products Table
#     cursor.execute('''
#         CREATE TABLE IF NOT EXISTS products (
#             id INTEGER PRIMARY KEY AUTOINCREMENT,
#             name TEXT NOT NULL,
#             price REAL NOT NULL,
#             quantity INTEGER NOT NULL,
#             image_name TEXT DEFAULT 'image_91e482.png',
#             variant_size TEXT DEFAULT 'N/A',
#             batch_number TEXT DEFAULT 'N/A',
#             mfg_date TEXT DEFAULT 'N/A',
#             expiry_date TEXT DEFAULT 'N/A',
#             registration_number TEXT DEFAULT 'N/A'
#         )
#     ''')
#     # Additive migration in case this is an older database that predates
#     # the registration_number column - existing data is preserved.
#     ensure_column(cursor, "products", "registration_number", "registration_number TEXT DEFAULT 'N/A'")

#     # Combined Transactions Table (Supports both INWARD stock additions and OUTWARD sales)
#     cursor.execute('''
#         CREATE TABLE IF NOT EXISTS inventory_transactions (
#             id INTEGER PRIMARY KEY AUTOINCREMENT,
#             timestamp TEXT NOT NULL,
#             transaction_type TEXT NOT NULL, -- 'INWARD' or 'OUTWARD'
#             product_name TEXT NOT NULL,
#             quantity INTEGER NOT NULL,
#             price_per_unit REAL NOT NULL,
#             total_amount REAL NOT NULL
#         )
#     ''')

#     # Product catalog: registered products the client sells, searchable when
#     # adding new stock so details don't have to be retyped from scratch.
#     cursor.execute('''
#         CREATE TABLE IF NOT EXISTS catalog_products (
#             id INTEGER PRIMARY KEY AUTOINCREMENT,
#             product_name TEXT NOT NULL,
#             model_size TEXT NOT NULL,
#             registration_number TEXT NOT NULL
#         )
#     ''')

#     # Invoice header records - gives every printed bill a real, sequential,
#     # auto-generated invoice number instead of the static "#AUTO-GEN" label.
#     cursor.execute('''
#         CREATE TABLE IF NOT EXISTS invoices (
#             id INTEGER PRIMARY KEY AUTOINCREMENT,
#             invoice_number TEXT NOT NULL,
#             timestamp TEXT NOT NULL,
#             customer_name TEXT DEFAULT '',
#             customer_phone TEXT DEFAULT '',
#             customer_address TEXT DEFAULT '',
#             gross_amount REAL DEFAULT 0,
#             discount_amount REAL DEFAULT 0,
#             sales_tax REAL DEFAULT 0,
#             net_total REAL DEFAULT 0
#         )
#     ''')
    
#     cursor.execute("SELECT COUNT(*) FROM products")
#     if cursor.fetchone()[0] == 0:
#         # Fresh install: no more placeholder/test products are seeded now that
#         # the real registered product catalog is in place. Inventory starts empty
#         # and gets populated via Inventory Intake using the catalog search.
#         pass

#     # One-time (and repeat-safe) cleanup: remove the old placeholder/test
#     # products that were only ever meant for demoing the app. Safe to run on
#     # every startup - it's a no-op once they're already gone, and it never
#     # touches real products or historical transaction records.
#     TEST_PRODUCT_NAMES = ["Premium Industrial Fitting A1", "High-Grade Core Coupler", "Trocar (10mm)"]
#     cursor.executemany("DELETE FROM products WHERE name = ?", [(n,) for n in TEST_PRODUCT_NAMES])

#     # Seed the product catalog once, on first run only
#     cursor.execute("SELECT COUNT(*) FROM catalog_products")
#     if cursor.fetchone()[0] == 0:
#         cursor.executemany(
#             "INSERT INTO catalog_products (product_name, model_size, registration_number) VALUES (?, ?, ?)",
#             CATALOG_SEED
#         )
    
#     conn.commit()
#     conn.close()
#     print("--> Database tracking matrices fully operational.")

# init_db()

# def generate_invoice_number(cursor):
#     """Sequential, auto-generated invoice number: SI-<year>-<0001>."""
#     year = datetime.now().year
#     cursor.execute("SELECT COUNT(*) FROM invoices WHERE invoice_number LIKE ?", (f"SI-{year}-%",))
#     seq = cursor.fetchone()[0] + 1
#     return f"SI-{year}-{seq:04d}"

# @app.route('/')
# def index():
#     return redirect(url_for('dashboard'))

# @app.route('/dashboard')
# def dashboard():
#     conn = get_db_connection()
#     cursor = conn.cursor()
    
#     # Fetch current layout of warehouse
#     cursor.execute("SELECT * FROM products")
#     all_products = [dict(row) for row in cursor.fetchall()]
    
#     # Fetch complete split transactions history log
#     cursor.execute("SELECT * FROM inventory_transactions ORDER BY id DESC")
#     all_transactions = [dict(row) for row in cursor.fetchall()]

#     # Full product catalog, passed to the template as JSON for the
#     # client-side search/autocomplete widget on the Inventory Intake tab.
#     cursor.execute("SELECT product_name, model_size, registration_number FROM catalog_products ORDER BY product_name, model_size")
#     catalog_rows = [dict(row) for row in cursor.fetchall()]
    
#     conn.close()

#     # --- Additive summary metrics (pro feature: quick summary cards) ---
#     total_products = len(all_products)
#     total_stock_units = sum(p['quantity'] for p in all_products)
#     total_stock_value = sum(p['quantity'] * p['price'] for p in all_products)
#     low_stock_items = [p for p in all_products if p['quantity'] <= LOW_STOCK_THRESHOLD]
#     low_stock_count = len(low_stock_items)

#     summary = {
#         "total_products": total_products,
#         "total_stock_units": total_stock_units,
#         "total_stock_value": total_stock_value,
#         "low_stock_count": low_stock_count,
#         "low_stock_items": low_stock_items
#     }

#     return render_template(
#         'dashboard.html',
#         products=all_products,
#         transactions=all_transactions,
#         summary=summary,
#         catalog_json=json.dumps(catalog_rows),
#         error_message=request.args.get('error')
#     )

# @app.route('/inward-stock', methods=['POST'])
# def inward_stock():
#     try:
#         action_type = request.form.get('action_type')
#         conn = get_db_connection()
#         cursor = conn.cursor()
#         current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
#         if action_type == 'existing':
#             product_id = request.form.get('product_id')
#             added_qty = int(request.form.get('quantity', 0))

#             if added_qty <= 0:
#                 conn.close()
#                 return jsonify({"status": "error", "message": "Quantity must be greater than zero."}), 400
            
#             # Retrieve name and rate info for ledger mapping
#             cursor.execute("SELECT name, price FROM products WHERE id = ?", (product_id,))
#             prod = cursor.fetchone()
#             if prod:
#                 cursor.execute("UPDATE products SET quantity = quantity + ? WHERE id = ?", (added_qty, product_id))
#                 total_cost = prod['price'] * added_qty
#                 cursor.execute('''
#                     INSERT INTO inventory_transactions (timestamp, transaction_type, product_name, quantity, price_per_unit, total_amount)
#                     VALUES (?, 'INWARD', ?, ?, ?, ?)
#                 ''', (current_time, prod['name'], added_qty, prod['price'], total_cost))
#                 conn.commit()
#                 conn.close()
#                 return jsonify({"status": "success", "message": f"Added {added_qty} units to {prod['name']}."})
#             else:
#                 conn.close()
#                 return jsonify({"status": "error", "message": "Selected product could not be found."}), 404
            
#         elif action_type == 'new':
#             raw_name = (request.form.get('name') or '').strip()
#             size = request.form.get('variant_size', 'N/A') or 'N/A'
#             qty = int(request.form.get('quantity', 0) or 0)
#             batch = request.form.get('batch_number', 'N/A') or 'N/A'
#             mfg = request.form.get('mfg_date', 'N/A') or 'N/A'
#             exp = request.form.get('expiry_date', 'N/A') or 'N/A'
#             registration = request.form.get('registration_number', 'N/A') or 'N/A'

#             if not raw_name:
#                 conn.close()
#                 return jsonify({"status": "error", "message": "Product name is required."}), 400
#             try:
#                 price = float(request.form.get('price', 0.0) or 0)
#             except ValueError:
#                 conn.close()
#                 return jsonify({"status": "error", "message": "Unit price must be a number."}), 400
#             if price <= 0 or qty <= 0:
#                 conn.close()
#                 return jsonify({"status": "error", "message": "Price and quantity must be greater than zero."}), 400
            
#             combined_name = f"{raw_name} ({size})" if size and size.lower() != 'n/a' else raw_name
            
#             image_filename = 'image_91e482.png'
#             if 'product_image' in request.files:
#                 file = request.files['product_image']
#                 if file and file.filename and allowed_file(file.filename):
#                     filename = secure_filename(file.filename)
#                     timestamp_str = datetime.now().strftime("%Y%m%d%H%M%S")
#                     filename = f"prod_{timestamp_str}_{filename}"
#                     file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
#                     image_filename = filename

#             cursor.execute('''
#                 INSERT INTO products (name, price, quantity, image_name, variant_size, batch_number, mfg_date, expiry_date, registration_number)
#                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
#             ''', (combined_name, price, qty, image_filename, size, batch, mfg, exp, registration))
            
#             total_cost = price * qty
#             cursor.execute('''
#                 INSERT INTO inventory_transactions (timestamp, transaction_type, product_name, quantity, price_per_unit, total_amount)
#                 VALUES (?, 'INWARD', ?, ?, ?, ?)
#             ''', (current_time, combined_name, qty, price, total_cost))
#             conn.commit()
#             conn.close()
#             return jsonify({"status": "success", "message": f"{combined_name} added to inventory."})

#         conn.close()
#         return jsonify({"status": "error", "message": "Unrecognized form action."}), 400
#     except Exception as e:
#         # JSON error instead of a raw browser error page - the billing/dashboard
#         # JS reads this and shows it as an on-screen toast.
#         return jsonify({"status": "error", "message": f"Could not save stock entry: {str(e)}"}), 500

# @app.route('/delete-product/<int:product_id>', methods=['POST'])
# def delete_product(product_id):
#     try:
#         conn = get_db_connection()
#         cursor = conn.cursor()
#         cursor.execute("SELECT name FROM products WHERE id = ?", (product_id,))
#         prod = cursor.fetchone()
#         if not prod:
#             conn.close()
#             return jsonify({"status": "error", "message": "That product no longer exists."}), 404
#         cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
#         conn.commit()
#         conn.close()
#         return jsonify({"status": "success", "message": f"{prod['name']} was deleted."})
#     except Exception as e:
#         return jsonify({"status": "error", "message": f"Could not delete product: {str(e)}"}), 500

# @app.route('/sales-analytics-data')
# def sales_analytics_data():
#     """Aggregates transaction nodes for generating interactive Chart framework maps."""
#     conn = get_db_connection()
#     cursor = conn.cursor()
#     cursor.execute("SELECT timestamp, total_amount, transaction_type FROM inventory_transactions")
#     rows = cursor.fetchall()
#     conn.close()

#     current_year = datetime.now().year

#     months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
#     monthly_sales = [0.0] * 12
#     monthly_inward = [0.0] * 12

#     for row in rows:
#         try:
#             date_obj = datetime.strptime(row['timestamp'].split()[0], "%Y-%m-%d")
#             if date_obj.year == current_year:
#                 month_idx = date_obj.month - 1
#                 if row['transaction_type'] == 'OUTWARD':
#                     monthly_sales[month_idx] += row['total_amount']
#                 elif row['transaction_type'] == 'INWARD':
#                     monthly_inward[month_idx] += row['total_amount']
#         except Exception:
#             continue

#     yearly_total_revenue = sum(monthly_sales)
#     yearly_total_inward = sum(monthly_inward)

#     return jsonify({
#         "labels": months,
#         "sales": monthly_sales,
#         "inward": monthly_inward,
#         "yearly_total_revenue": yearly_total_revenue,
#         "yearly_total_inward": yearly_total_inward,
#         "yearly_gross_margin": yearly_total_revenue - yearly_total_inward,
#         "year": current_year
#     })

# @app.route('/sales-report-data')
# def sales_report_data():
#     """Advanced report engine behind the Sales & Inventory Trends section.
#     Accepts optional ?start=YYYY-MM-DD&end=YYYY-MM-DD and aggregates real
#     figures for that exact window: totals, margin, transaction count, and
#     top products by revenue and by quantity moved."""
#     start = request.args.get('start')
#     end = request.args.get('end')

#     conn = get_db_connection()
#     cursor = conn.cursor()
#     cursor.execute("SELECT timestamp, transaction_type, product_name, quantity, total_amount FROM inventory_transactions ORDER BY timestamp ASC")
#     rows = [dict(row) for row in cursor.fetchall()]
#     conn.close()

#     def in_range(ts):
#         try:
#             d = datetime.strptime(ts.split()[0], "%Y-%m-%d").date()
#         except Exception:
#             return False
#         if start:
#             try:
#                 if d < datetime.strptime(start, "%Y-%m-%d").date():
#                     return False
#             except Exception:
#                 pass
#         if end:
#             try:
#                 if d > datetime.strptime(end, "%Y-%m-%d").date():
#                     return False
#             except Exception:
#                 pass
#         return True

#     filtered = [r for r in rows if in_range(r['timestamp'])]

#     total_sales = sum(r['total_amount'] for r in filtered if r['transaction_type'] == 'OUTWARD')
#     total_purchases = sum(r['total_amount'] for r in filtered if r['transaction_type'] == 'INWARD')
#     sale_rows = [r for r in filtered if r['transaction_type'] == 'OUTWARD']
#     transaction_count = len(sale_rows)
#     average_sale = (total_sales / transaction_count) if transaction_count else 0.0

#     product_stats = {}
#     for r in sale_rows:
#         p = product_stats.setdefault(r['product_name'], {"revenue": 0.0, "qty": 0})
#         p["revenue"] += r['total_amount']
#         p["qty"] += r['quantity']

#     top_by_revenue = sorted(
#         [{"name": k, "revenue": v["revenue"], "qty": v["qty"]} for k, v in product_stats.items()],
#         key=lambda x: x["revenue"], reverse=True
#     )[:5]

#     # Daily trend within the selected window, for a chart that actually
#     # reflects a custom date range instead of only fixed month buckets.
#     daily = {}
#     for r in filtered:
#         day = r['timestamp'].split()[0]
#         bucket = daily.setdefault(day, {"sales": 0.0, "purchases": 0.0})
#         if r['transaction_type'] == 'OUTWARD':
#             bucket["sales"] += r['total_amount']
#         else:
#             bucket["purchases"] += r['total_amount']
#     daily_sorted = sorted(daily.items(), key=lambda x: x[0])

#     return jsonify({
#         "start": start,
#         "end": end,
#         "total_sales": total_sales,
#         "total_purchases": total_purchases,
#         "gross_margin": total_sales - total_purchases,
#         "transaction_count": transaction_count,
#         "average_sale": average_sale,
#         "top_products": top_by_revenue,
#         "daily_labels": [d for d, _ in daily_sorted],
#         "daily_sales": [v["sales"] for _, v in daily_sorted],
#         "daily_purchases": [v["purchases"] for _, v in daily_sorted]
#     })

# @app.route('/export-sales-report-csv')
# def export_sales_report_csv():
#     """Downloadable CSV version of the same report, for the selected date range."""
#     start = request.args.get('start')
#     end = request.args.get('end')

#     conn = get_db_connection()
#     cursor = conn.cursor()
#     cursor.execute("SELECT timestamp, transaction_type, product_name, quantity, price_per_unit, total_amount FROM inventory_transactions ORDER BY timestamp ASC")
#     rows = [dict(row) for row in cursor.fetchall()]
#     conn.close()

#     def in_range(ts):
#         try:
#             d = datetime.strptime(ts.split()[0], "%Y-%m-%d").date()
#         except Exception:
#             return False
#         if start:
#             try:
#                 if d < datetime.strptime(start, "%Y-%m-%d").date():
#                     return False
#             except Exception:
#                 pass
#         if end:
#             try:
#                 if d > datetime.strptime(end, "%Y-%m-%d").date():
#                     return False
#             except Exception:
#                 pass
#         return True

#     filtered = [r for r in rows if in_range(r['timestamp'])]
#     total_sales = sum(r['total_amount'] for r in filtered if r['transaction_type'] == 'OUTWARD')
#     total_purchases = sum(r['total_amount'] for r in filtered if r['transaction_type'] == 'INWARD')

#     output = io.StringIO()
#     writer = csv.writer(output)
#     writer.writerow([f"Sanyoh International - Sales & Inventory Report"])
#     writer.writerow([f"Period: {start or 'All time'} to {end or 'Present'}"])
#     writer.writerow([])
#     writer.writerow(["Date & Time", "Type", "Product", "Quantity", "Unit Price", "Total Amount"])
#     for r in filtered:
#         writer.writerow([r['timestamp'], r['transaction_type'], r['product_name'], r['quantity'], r['price_per_unit'], r['total_amount']])
#     writer.writerow([])
#     writer.writerow(["Total Sales", "", "", "", "", round(total_sales, 2)])
#     writer.writerow(["Total Purchases", "", "", "", "", round(total_purchases, 2)])
#     writer.writerow(["Gross Margin", "", "", "", "", round(total_sales - total_purchases, 2)])

#     filename = f"sales_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
#     return Response(
#         output.getvalue(),
#         mimetype="text/csv",
#         headers={"Content-Disposition": f"attachment; filename={filename}"}
#     )

# @app.route('/print-transaction/<int:transaction_id>')
# def print_transaction(transaction_id):
#     """Standalone, print-friendly single-transaction receipt (Transaction
#     History -> individual print). Branded the same as the main invoice."""
#     conn = get_db_connection()
#     cursor = conn.cursor()
#     cursor.execute("SELECT * FROM inventory_transactions WHERE id = ?", (transaction_id,))
#     tx = cursor.fetchone()
#     conn.close()

#     if not tx:
#         return "Transaction not found.", 404

#     return render_template(
#         'print_transaction.html',
#         tx=dict(tx),
#         company=COMPANY_INFO
#     )

# @app.route('/export-inventory-csv')
# def export_inventory_csv():
#     """Pro feature addition: one-click downloadable inventory report."""
#     conn = get_db_connection()
#     cursor = conn.cursor()
#     cursor.execute("SELECT * FROM products")
#     rows = [dict(row) for row in cursor.fetchall()]
#     conn.close()

#     output = io.StringIO()
#     writer = csv.writer(output)
#     writer.writerow(["Product Name", "Registration #", "Batch Number", "Unit Price", "Quantity", "Stock Value", "Mfg Date", "Expiry Date"])
#     for p in rows:
#         writer.writerow([
#             p['name'], p.get('registration_number', 'N/A'), p['batch_number'], p['price'], p['quantity'],
#             round(p['price'] * p['quantity'], 2), p['mfg_date'], p['expiry_date']
#         ])

#     filename = f"inventory_report_{datetime.now().strftime('%Y%m%d')}.csv"
#     return Response(
#         output.getvalue(),
#         mimetype="text/csv",
#         headers={"Content-Disposition": f"attachment; filename={filename}"}
#     )

# @app.route('/bill')
# def bill():
#     conn = get_db_connection()
#     cursor = conn.cursor()
#     # Fetch every field the invoice needs directly - previously this only
#     # selected name/price/quantity, which is why batch/mfg/expiry never
#     # actually showed up on the printed bill even though the data existed.
#     cursor.execute("SELECT name, price, quantity, batch_number, mfg_date, expiry_date, registration_number FROM products")
#     all_items = cursor.fetchall()

#     next_invoice_number = generate_invoice_number(cursor)
#     conn.close()
#     return render_template(
#         'bill.html',
#         items=all_items,
#         company=COMPANY_INFO,
#         default_tax_percent=DEFAULT_TAX_PERCENT,
#         next_invoice_number=next_invoice_number
#     )

# @app.route('/checkout', methods=['POST'])
# def checkout():
#     try:
#         data = request.get_json()
#         cart = data.get('cart', [])
#         sales_tax = float(data.get('sales_tax', 0) or 0)
#         customer = data.get('customer', {}) or {}

#         conn = get_db_connection()
#         cursor = conn.cursor()
#         current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

#         gross_amount_total = 0.0
#         discount_amount_total = 0.0
        
#         for item in cart:
#             # Query rate to create matching analytical logging footprint
#             cursor.execute("SELECT price FROM products WHERE name = ?", (item['name'],))
#             prod_row = cursor.fetchone()
#             price_val = prod_row['price'] if prod_row else 0.0

#             discount_percent = float(item.get('discount_percent', 0) or 0)
#             gross_amount = price_val * int(item['qty'])
#             line_discount = gross_amount * (discount_percent / 100)
#             total_sales_amount = gross_amount - line_discount

#             gross_amount_total += gross_amount
#             discount_amount_total += line_discount
            
#             cursor.execute("UPDATE products SET quantity = MAX(0, quantity - ?) WHERE name = ?", (item['qty'], item['name']))
            
#             cursor.execute('''
#                 INSERT INTO inventory_transactions (timestamp, transaction_type, product_name, quantity, price_per_unit, total_amount)
#                 VALUES (?, 'OUTWARD', ?, ?, ?, ?)
#             ''', (current_time, item['name'], item['qty'], price_val, total_sales_amount))

#         net_total = (gross_amount_total - discount_amount_total) + sales_tax

#         # Auto-generate the sequential invoice number and persist the invoice header
#         invoice_number = generate_invoice_number(cursor)
#         cursor.execute('''
#             INSERT INTO invoices (invoice_number, timestamp, customer_name, customer_phone, customer_address, gross_amount, discount_amount, sales_tax, net_total)
#             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
#         ''', (
#             invoice_number, current_time,
#             customer.get('name', ''), customer.get('phone', ''), customer.get('address', ''),
#             gross_amount_total, discount_amount_total, sales_tax, net_total
#         ))
            
#         conn.commit()
#         conn.close()
#         return jsonify({"status": "success", "invoice_number": invoice_number})
#     except Exception as e:
#         return jsonify({"status": "error", "message": str(e)}), 500

# @app.route('/login', methods=['GET', 'POST'])
# def login():
#     if request.method == 'POST':
#         session['user'] = request.form.get('email')
#         return redirect(url_for('dashboard'))
#     return render_template('login.html', error=None)

# @app.route('/logout')
# def logout():
#     session.clear()
#     return render_template("logout.html")

# if __name__ == '__main__':
#     app.run(debug=True, port=5000)




