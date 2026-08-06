from flask import Flask, render_template, request, jsonify, send_file
import os
import threading
import sys
import webbrowser
import io
import time
import json
import re
import traceback
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# =============================
# Resource Path (for EXE build)
# =============================
def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)

# =============================
# Flask Setup
# =============================
app = Flask(
    __name__,
    template_folder=resource_path("templates"),
    static_folder=resource_path("static")
)

# =============================
# Global Exception Handler
# =============================
@app.errorhandler(Exception)
def handle_exception(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    print("\n" + "=" * 60)
    print("BACKEND EXCEPTION DETECTED - FULL TRACEBACK:")
    traceback.print_exc()
    print("=" * 60 + "\n")
    app.logger.error(f"Backend Exception: {str(e)}", exc_info=True)
    return jsonify({
        "success": False,
        "error": f"Server Error: {str(e)}"
    }), 500

# =============================
# Database Connection Helpers
# =============================
def get_db_url():
    url = os.getenv("DATABASE_URL")
    if not url:
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                txt = f.read().strip()
                for line in txt.splitlines():
                    line = line.strip()
                    if line.startswith("DATABASE_URL="):
                        url = line.split("DATABASE_URL=", 1)[1].strip().strip("'\"")
                        break
                    elif line.startswith("postgresql://") or line.startswith("postgres://"):
                        url = line
                        break
    if not url:
        raise RuntimeError("DATABASE_URL not found in environment or .env file.")
    return url

def get_db_connection():
    url = get_db_url()
    return psycopg2.connect(url)

# =============================
# Helper Utilities
# =============================
def parse_gst_float(gst_val):
    if gst_val is None:
        return 5.0
    gst_str = str(gst_val).replace("%", "").strip()
    try:
        return float(gst_str)
    except Exception:
        return 5.0

def extract_customer_info(html_data):
    cust_name = ""
    cust_phone = ""
    if html_data:
        m_name = re.search(r'id=["\']custName["\'][^>]*>(.*?)</span>', html_data, re.IGNORECASE)
        if m_name:
            raw = m_name.group(1)
            cust_name = re.sub(r'<[^>]+>', '', raw).strip()
        
        m_phone = re.search(r'id=["\']custMobile["\'][^>]*>(.*?)</span>', html_data, re.IGNORECASE)
        if m_phone:
            raw = m_phone.group(1)
            cust_phone = re.sub(r'<[^>]+>', '', raw).strip()
    return cust_name, cust_phone

def extract_items_list(bill_items_json):
    if not bill_items_json:
        return []
    try:
        if isinstance(bill_items_json, (dict, list)):
            data = bill_items_json
        else:
            data = json.loads(bill_items_json)
        if isinstance(data, list):
            return data
        elif isinstance(data, dict):
            return data.get("items", [])
    except Exception:
        pass
    return []

def calculate_bill_totals(items):
    subtotal = 0.0
    gst_total = 0.0
    for item in items:
        try:
            qty = float(item.get("qty", 0))
            rate = float(item.get("rate", 0))
            gst_str = str(item.get("gst", "5")).replace("%", "").strip()
            gst_pct = float(gst_str) if gst_str else 5.0
            
            item_sub = qty * rate
            item_gst = item_sub * (gst_pct / 100.0)
            
            subtotal += item_sub
            gst_total += item_gst
        except Exception:
            pass
    grand_total = subtotal + gst_total
    return round(subtotal, 2), round(gst_total, 2), round(grand_total, 2)

# =============================
# Supabase DB Product Functions
# =============================
def load_products():
    products = []
    current_month = time.strftime("%Y-%m")
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT product_id, product_name, purchase_quantity, sold_quantity, 
                           purchase_date, unit_price, cost_price, gst, notes, monthly_sold
                    FROM products
                    ORDER BY product_name ASC
                """)
                rows = cur.fetchall()
                for row in rows:
                    p_id = str(row["product_id"])
                    p_name = str(row["product_name"] or "")
                    p_qty = int(row["purchase_quantity"]) if row["purchase_quantity"] is not None else 0
                    s_qty = int(row["sold_quantity"]) if row["sold_quantity"] is not None else 0
                    p_date = str(row["purchase_date"]) if row["purchase_date"] is not None else ""
                    
                    u_price = float(row["unit_price"]) if row["unit_price"] is not None else None
                    c_price = float(row["cost_price"]) if row["cost_price"] is not None else 0.0
                    
                    gst_val = row["gst"]
                    gst_str = f"{float(gst_val):g}%" if gst_val is not None else "5%"
                    notes = str(row["notes"] or "")
                    
                    monthly_json = row["monthly_sold"]
                    monthly_sold_qty = 0
                    if isinstance(monthly_json, dict):
                        monthly_sold_qty = int(monthly_json.get(current_month, 0))
                    elif isinstance(monthly_json, str):
                        try:
                            m_dict = json.loads(monthly_json)
                            monthly_sold_qty = int(m_dict.get(current_month, 0))
                        except Exception:
                            monthly_sold_qty = 0
                    
                    products.append({
                        "id": p_id,
                        "name": p_name,
                        "purchase_qty": p_qty,
                        "sold_qty": s_qty,
                        "purchase_date": p_date,
                        "unit_price": u_price,
                        "notes": notes,
                        "gst": gst_str,
                        "cost_price": c_price,
                        "monthly_sold": monthly_sold_qty
                    })
    except Exception as e:
        app.logger.error(f"Error loading products from Supabase: {e}")
    return products

def save_product_db(name, purchase_qty, sold_qty, purchase_date, unit_price, notes, cost_price=0.0, gst="5%"):
    product_id = str(int(time.time() * 1000))
    gst_float = parse_gst_float(gst)
    p_date = purchase_date if (purchase_date and purchase_date.strip()) else None

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO products (
                    product_id, product_name, purchase_quantity, sold_quantity,
                    purchase_date, unit_price, cost_price, gst, notes, monthly_sold,
                    created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            """, (
                product_id, name, purchase_qty, sold_qty, p_date,
                unit_price if unit_price is not None else 0.0,
                cost_price if cost_price is not None else 0.0,
                gst_float, notes or "", json.dumps({})
            ))
        conn.commit()
    return product_id

def update_product_db(product_id, name, purchase_qty, sold_qty, purchase_date, unit_price, notes, cost_price=0.0, gst="5%"):
    gst_float = parse_gst_float(gst)
    p_date = purchase_date if (purchase_date and purchase_date.strip()) else None

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE products
                SET product_name = %s,
                    purchase_quantity = %s,
                    sold_quantity = %s,
                    purchase_date = %s,
                    unit_price = %s,
                    cost_price = %s,
                    gst = %s,
                    notes = %s,
                    updated_at = NOW()
                WHERE product_id = %s
            """, (
                name, purchase_qty, sold_qty, p_date,
                unit_price if unit_price is not None else 0.0,
                cost_price if cost_price is not None else 0.0,
                gst_float, notes or "", product_id
            ))
            found = (cur.rowcount > 0)
        conn.commit()
    return found

def delete_product_db(product_id):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM products WHERE product_id = %s", (product_id,))
            deleted = (cur.rowcount > 0)
        conn.commit()
    return deleted

def validate_product_data(data):
    name = data.get("name")
    purchase_qty = data.get("purchase_qty")
    sold_qty = data.get("sold_qty", 0)

    if not name or str(name).strip() == "":
        return False, "Product Name is required"

    try:
        purchase_qty = int(purchase_qty)
        if purchase_qty < 0:
            return False, "Purchase Quantity cannot be negative"
    except (ValueError, TypeError):
        return False, "Invalid Purchase Quantity"

    try:
        sold_qty = int(sold_qty)
        if sold_qty < 0:
            return False, "Sold Quantity cannot be negative"
    except (ValueError, TypeError):
        return False, "Invalid Sold Quantity"

    if sold_qty > purchase_qty:
        return False, "Sold Quantity cannot be greater than Purchased Quantity"

    unit_price = data.get("unit_price")
    if unit_price is not None and unit_price != "":
        try:
            unit_price = float(unit_price)
            if unit_price < 0:
                return False, "Selling Price (Unit Price) cannot be negative"
        except ValueError:
            return False, "Invalid Selling Price"

    cost_price = data.get("cost_price")
    if cost_price is not None and cost_price != "":
        try:
            cost_price = float(cost_price)
            if cost_price < 0:
                return False, "Cost Price cannot be negative"
        except ValueError:
            return False, "Invalid Cost Price"

    return True, ""

# =============================
# Inventory Stock Transaction Helpers
# =============================
def apply_inventory_deltas_in_db(cur, item_deltas):
    """
    item_deltas: dict of { product_name_lower: delta_int }
    Modifies sold_quantity and monthly_sold using an active cursor within a database transaction.
    """
    if not item_deltas:
        return
    current_month = time.strftime("%Y-%m")

    for name_lower, delta in item_deltas.items():
        cur.execute("""
            SELECT product_id, purchase_quantity, sold_quantity, monthly_sold
            FROM products
            WHERE LOWER(TRIM(product_name)) = %s
            FOR UPDATE
        """, (name_lower,))
        row = cur.fetchone()
        if row:
            p_id, purchase_qty, current_sold, monthly_json = row
            purchase_qty = purchase_qty or 0
            current_sold = current_sold or 0

            new_sold = max(0, current_sold + delta)
            new_sold = min(new_sold, purchase_qty)

            if isinstance(monthly_json, dict):
                m_dict = monthly_json
            elif isinstance(monthly_json, str):
                try:
                    m_dict = json.loads(monthly_json)
                except Exception:
                    m_dict = {}
            else:
                m_dict = {}

            cur_month_val = int(m_dict.get(current_month, 0))
            new_month_val = max(0, cur_month_val + delta)
            m_dict[current_month] = new_month_val

            cur.execute("""
                UPDATE products
                SET sold_quantity = %s,
                    monthly_sold = %s,
                    updated_at = NOW()
                WHERE product_id = %s
            """, (new_sold, json.dumps(m_dict), p_id))

# =============================
# Supabase DB Invoice Functions
# =============================
def load_invoices():
    invoices = []
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT invoice_id, file_name, html_data, bill_items, status
                    FROM invoices
                    WHERE status NOT IN ('Cancelled', 'Deleted')
                    ORDER BY created_at DESC
                """)
                rows = cur.fetchall()
                for r in rows:
                    b_items = r["bill_items"]
                    if isinstance(b_items, (dict, list)):
                        b_items_str = json.dumps(b_items)
                    else:
                        b_items_str = str(b_items or "")
                    
                    invoices.append({
                        "id": str(r["invoice_id"]),
                        "name": str(r["file_name"] or ""),
                        "data": str(r["html_data"] or ""),
                        "bill_items": b_items_str,
                        "status": str(r["status"] or "Active")
                    })
    except Exception as e:
        app.logger.error(f"Error loading invoices from Supabase: {e}")
    return invoices

# =============================
# Routes
# =============================

@app.route("/")
def index():
    return render_template("index.html")

# Get all invoices (sidebar)
@app.route("/get-invoices")
def get_invoices():
    invoices = load_invoices()
    return jsonify(invoices)

# Get one invoice
@app.route("/get-invoice/<invoice_id>")
def get_invoice(invoice_id):
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT invoice_id, file_name, html_data, bill_items, status
                    FROM invoices
                    WHERE invoice_id = %s
                """, (str(invoice_id),))
                r = cur.fetchone()
                if r:
                    b_items = r["bill_items"]
                    if isinstance(b_items, (dict, list)):
                        b_items_str = json.dumps(b_items)
                    else:
                        b_items_str = str(r["bill_items"] or "")
                    return jsonify({
                        "id": str(r["invoice_id"]),
                        "name": str(r["file_name"] or ""),
                        "data": str(r["html_data"] or ""),
                        "bill_items": b_items_str,
                        "status": str(r["status"] or "Active")
                    })
    except Exception as e:
        app.logger.error(f"Error getting invoice {invoice_id}: {e}")
    return jsonify({"data": ""})

# Cancel invoice (uses single DB transaction for status update + inventory restore)
@app.route("/cancel-invoice", methods=["POST"])
def cancel_invoice():
    data = request.get_json(silent=True) or {}
    cancel_id = data.get("id")
    if not cancel_id:
        return jsonify({"success": False, "error": "Missing invoice ID"}), 400

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT bill_items FROM invoices WHERE invoice_id = %s FOR UPDATE", (str(cancel_id),))
            row = cur.fetchone()
            if row and row[0]:
                old_items = extract_items_list(row[0])
                old_deltas = {}
                for item in old_items:
                    name = item.get("name", "").strip().lower()
                    qty = int(float(item.get("qty", 0)))
                    if name and qty > 0:
                        old_deltas[name] = old_deltas.get(name, 0) - qty
                apply_inventory_deltas_in_db(cur, old_deltas)

            cur.execute("UPDATE invoices SET status = 'Cancelled', updated_at = NOW() WHERE invoice_id = %s", (str(cancel_id),))
        conn.commit()
    except Exception as e:
        conn.rollback()
        app.logger.error(f"Error cancelling invoice {cancel_id}: {e}")
        return jsonify({"success": False, "error": f"Error cancelling invoice: {str(e)}"}), 400
    finally:
        conn.close()

    return jsonify({"success": True, "message": "Invoice cancelled"})

# Save new invoice (uses single DB transaction for product validation + invoice insert + stock deduction)
@app.route("/save", methods=["POST"])
def save():
    data = request.get_json(silent=True) or {}
    file_name = data.get("name", "Untitled Bill")
    html_data = data.get("data", "")
    bill_items = data.get("bill_items", [])
    payment_method = data.get("payment_method", "Cash")

    if not bill_items or not isinstance(bill_items, list):
        if isinstance(bill_items, str):
            try:
                bill_items = json.loads(bill_items)
            except Exception:
                bill_items = []

    if not bill_items:
        return jsonify({"success": False, "error": "No valid products in bill"}), 400

    payload = {
        "payment_method": payment_method,
        "items": bill_items
    }
    bill_items_json = json.dumps(payload)

    invoice_id = str(int(time.time() * 1000))
    cust_name, cust_phone = extract_customer_info(html_data)
    subtotal, gst_total, grand_total = calculate_bill_totals(bill_items)
    today_date = time.strftime("%Y-%m-%d")
    now_time = time.strftime("%H:%M:%S")

    deltas = {}
    for item in bill_items:
        name = item.get("name", "").strip()
        qty = int(float(item.get("qty", 0)))
        if name and qty > 0:
            deltas[name.lower()] = deltas.get(name.lower(), 0) + qty

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Step 1: Validate Product Existence and Stock in Supabase products table
            for name_lower, req_qty in deltas.items():
                cur.execute("""
                    SELECT product_name, purchase_quantity, sold_quantity
                    FROM products
                    WHERE LOWER(TRIM(product_name)) = %s
                    FOR UPDATE
                """, (name_lower,))
                row = cur.fetchone()
                if not row:
                    conn.rollback()
                    print(f"--> [SAVE REJECTED] Product '{name_lower}' does not exist in Supabase products table.")
                    return jsonify({"success": False, "error": f"Product '{name_lower}' does not exist in inventory."}), 400

                p_real_name, p_qty, s_qty = row
                p_qty = p_qty or 0
                s_qty = s_qty or 0
                available_stock = p_qty - s_qty

                if req_qty > available_stock:
                    conn.rollback()
                    print(f"--> [SAVE REJECTED] Insufficient stock for '{p_real_name}'. Available: {available_stock}, Requested: {req_qty}")
                    return jsonify({"success": False, "error": f"Insufficient stock for '{p_real_name}'. Available: {available_stock}, Requested: {req_qty}"}), 400

            # Step 2: Insert invoice into Supabase invoices table
            cur.execute("""
                INSERT INTO invoices (
                    invoice_id, file_name, html_data, bill_items, payment_method,
                    customer_name, customer_phone, subtotal, gst_total, grand_total,
                    status, bill_date, bill_time, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            """, (
                invoice_id, file_name, html_data, bill_items_json, payment_method,
                cust_name, cust_phone, subtotal, gst_total, grand_total,
                "Active", today_date, now_time
            ))

            # Step 3: Deduct product stock within the SAME transaction
            apply_inventory_deltas_in_db(cur, deltas)

        conn.commit()
    except Exception as e:
        conn.rollback()
        print("\n" + "=" * 60)
        print("CRITICAL EXCEPTION IN POST /save:")
        traceback.print_exc()
        print("=" * 60 + "\n")
        app.logger.error(f"Error saving invoice to Supabase: {e}", exc_info=True)
        return jsonify({"success": False, "error": f"Database transaction failed: {str(e)}"}), 400
    finally:
        conn.close()

    return jsonify({
        "success": True,
        "message": "Invoice saved",
        "invoice_id": invoice_id
    })

# Update invoice (uses single DB transaction for old stock revert + invoice update + new stock deduction)
@app.route("/update-invoice", methods=["POST"])
def update_invoice():
    data = request.get_json(silent=True) or {}
    invoice_id = data.get("id")
    if not invoice_id:
        return jsonify({"success": False, "error": "Missing invoice ID for update"}), 400

    file_name = data.get("name", "")
    html_data = data.get("data", "")
    bill_items = data.get("bill_items", [])
    payment_method = data.get("payment_method", "Cash")

    if not bill_items or not isinstance(bill_items, list):
        if isinstance(bill_items, str):
            try:
                bill_items = json.loads(bill_items)
            except Exception:
                bill_items = []

    payload = {
        "payment_method": payment_method,
        "items": bill_items
    }
    new_bill_items_json = json.dumps(payload)
    cust_name, cust_phone = extract_customer_info(html_data)
    subtotal, gst_total, grand_total = calculate_bill_totals(bill_items)

    new_deltas = {}
    for item in bill_items:
        name = item.get("name", "").strip()
        qty = int(float(item.get("qty", 0)))
        if name and qty > 0:
            new_deltas[name.lower()] = new_deltas.get(name.lower(), 0) + qty

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT bill_items FROM invoices WHERE invoice_id = %s FOR UPDATE", (str(invoice_id),))
            row = cur.fetchone()
            old_deltas = {}
            if row and row[0]:
                old_items = extract_items_list(row[0])
                for item in old_items:
                    name = item.get("name", "").strip().lower()
                    qty = int(float(item.get("qty", 0)))
                    if name and qty > 0:
                        old_deltas[name] = old_deltas.get(name, 0) - qty

            # Validate Product Existence and Available Stock
            for name_lower, req_qty in new_deltas.items():
                cur.execute("""
                    SELECT product_name, purchase_quantity, sold_quantity
                    FROM products
                    WHERE LOWER(TRIM(product_name)) = %s
                    FOR UPDATE
                """, (name_lower,))
                p_row = cur.fetchone()
                if not p_row:
                    conn.rollback()
                    return jsonify({"success": False, "error": f"Product '{name_lower}' does not exist in inventory."}), 400

                p_real_name, p_qty, s_qty = p_row
                p_qty = p_qty or 0
                s_qty = s_qty or 0
                reverted_qty = abs(old_deltas.get(name_lower, 0))
                effective_available = (p_qty - s_qty) + reverted_qty

                if req_qty > effective_available:
                    conn.rollback()
                    return jsonify({"success": False, "error": f"Insufficient stock for '{p_real_name}'. Available: {effective_available}, Requested: {req_qty}"}), 400

            # 1. Revert old stock
            if old_deltas:
                apply_inventory_deltas_in_db(cur, old_deltas)

            # 2. Update invoice
            cur.execute("""
                UPDATE invoices
                SET file_name = %s,
                    html_data = %s,
                    bill_items = %s,
                    payment_method = %s,
                    customer_name = %s,
                    customer_phone = %s,
                    subtotal = %s,
                    gst_total = %s,
                    grand_total = %s,
                    updated_at = NOW()
                WHERE invoice_id = %s
            """, (
                file_name, html_data, new_bill_items_json, payment_method,
                cust_name, cust_phone, subtotal, gst_total, grand_total,
                str(invoice_id)
            ))

            # 3. Deduct new stock
            apply_inventory_deltas_in_db(cur, new_deltas)

        conn.commit()
    except Exception as e:
        conn.rollback()
        print("\n" + "=" * 60)
        print(f"CRITICAL EXCEPTION IN POST /update-invoice FOR ID {invoice_id}:")
        traceback.print_exc()
        print("=" * 60 + "\n")
        app.logger.error(f"Error updating invoice {invoice_id}: {e}", exc_info=True)
        return jsonify({"success": False, "error": f"Database transaction failed: {str(e)}"}), 400
    finally:
        conn.close()

    return jsonify({"success": True, "message": "Invoice updated"})

# PDF relay (used for printing)
@app.route("/pdf-action", methods=["POST"])
def pdf_action():
    if "file" not in request.files:
        return jsonify({"success": False, "error": "No file provided"}), 400
    file = request.files["file"]
    pdf_bytes = file.read()
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=False,
        download_name="invoice.pdf"
    )

@app.route("/rename-invoice", methods=["POST"])
def rename_invoice():
    data = request.get_json(silent=True) or {}
    old_id = data.get("old_id")
    new_id = data.get("new_id")
    if not old_id or not new_id:
        return jsonify({"success": False, "error": "Missing parameters"}), 400

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE invoices SET invoice_id = %s, updated_at = NOW() WHERE invoice_id = %s", (str(new_id), str(old_id)))
        conn.commit()
    return jsonify({"success": True, "message": "Renamed"})

@app.route("/delete-invoice", methods=["POST"])
def delete_invoice():
    data = request.get_json(silent=True) or {}
    delete_id = data.get("id")
    if not delete_id:
        return jsonify({"success": False, "error": "Missing invoice ID"}), 400

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT bill_items FROM invoices WHERE invoice_id = %s FOR UPDATE", (str(delete_id),))
            row = cur.fetchone()
            if row and row[0]:
                old_items = extract_items_list(row[0])
                old_deltas = {}
                for item in old_items:
                    name = item.get("name", "").strip().lower()
                    qty = int(float(item.get("qty", 0)))
                    if name and qty > 0:
                        old_deltas[name] = old_deltas.get(name, 0) - qty
                apply_inventory_deltas_in_db(cur, old_deltas)

            cur.execute("UPDATE invoices SET status = 'Deleted', updated_at = NOW() WHERE invoice_id = %s", (str(delete_id),))
        conn.commit()
    except Exception as e:
        conn.rollback()
        app.logger.error(f"Error deleting invoice {delete_id}: {e}", exc_info=True)
        return jsonify({"success": False, "error": f"Database transaction failed: {str(e)}"}), 400
    finally:
        conn.close()

    return jsonify({"success": True, "message": "Deleted"})

# =============================
# Inventory Routes
# =============================

@app.route("/inventory")
def inventory_page():
    return render_template("inventory.html")

@app.route("/get-products")
def get_products():
    return jsonify(load_products())

@app.route("/get-product-names")
def get_product_names():
    """Simplified product list for billing dropdown and stock validation."""
    products = load_products()
    result = []
    for p in products:
        available = p["purchase_qty"] - p["sold_qty"]
        result.append({
            "id": p["id"],
            "name": p["name"],
            "available_qty": available,
            "unit_price": p["unit_price"],
            "cost_price": p["cost_price"],
            "gst": p["gst"]
        })
    return jsonify(result)

@app.route("/save-product", methods=["POST"])
def save_product_route():
    data = request.get_json(silent=True) or {}
    is_valid, msg = validate_product_data(data)
    if not is_valid:
        return jsonify({"success": False, "error": msg}), 400

    name = str(data.get("name")).strip()
    purchase_qty = int(data.get("purchase_qty"))
    sold_qty = int(data.get("sold_qty", 0))
    purchase_date = str(data.get("purchase_date", ""))
    unit_price = data.get("unit_price")
    if unit_price is not None and unit_price != "":
        unit_price = float(unit_price)
    else:
        unit_price = None
    cost_price = data.get("cost_price")
    if cost_price is not None and cost_price != "":
        cost_price = float(cost_price)
    else:
        cost_price = 0.0
    notes = str(data.get("notes", ""))
    gst = str(data.get("gst", "5%")).strip()

    product_id = save_product_db(name, purchase_qty, sold_qty, purchase_date, unit_price, notes, cost_price, gst)
    return jsonify({"success": True, "message": "Product saved successfully", "product_id": product_id})

@app.route("/update-product", methods=["POST"])
def update_product_route():
    data = request.get_json(silent=True) or {}
    is_valid, msg = validate_product_data(data)
    if not is_valid:
        return jsonify({"success": False, "error": msg}), 400

    product_id = data.get("id")
    if not product_id:
        return jsonify({"success": False, "error": "Product ID is required"}), 400

    name = str(data.get("name")).strip()
    purchase_qty = int(data.get("purchase_qty"))
    sold_qty = int(data.get("sold_qty", 0))
    purchase_date = str(data.get("purchase_date", ""))
    unit_price = data.get("unit_price")
    if unit_price is not None and unit_price != "":
        unit_price = float(unit_price)
    else:
        unit_price = None
    cost_price = data.get("cost_price")
    if cost_price is not None and cost_price != "":
        cost_price = float(cost_price)
    else:
        cost_price = 0.0
    notes = str(data.get("notes", ""))
    gst = str(data.get("gst", "5%")).strip()

    found = update_product_db(product_id, name, purchase_qty, sold_qty, purchase_date, unit_price, notes, cost_price, gst)
    if found:
        return jsonify({"success": True, "message": "Product updated successfully"})
    else:
        return jsonify({"success": False, "error": "Product not found"}), 404

@app.route("/delete-product", methods=["POST"])
def delete_product_route():
    data = request.get_json(silent=True) or {}
    product_id = data.get("id")
    if not product_id:
        return jsonify({"success": False, "error": "Product ID is required"}), 400

    deleted = delete_product_db(product_id)
    if deleted:
        return jsonify({"success": True, "message": "Product deleted successfully"})
    else:
        return jsonify({"success": False, "error": "Product not found"}), 404

# =============================
# History Routes
# =============================

@app.route("/history")
def history_page():
    return render_template("history.html")

@app.route("/get-history-data")
def get_history_data():
    invoices = load_invoices()
    products = load_products()

    prod_map = {}
    for p in products:
        prod_map[p["name"].strip().lower()] = p

    today_str = time.strftime("%Y-%m-%d")
    current_month_str = time.strftime("%Y-%m")
    target_month_str = request.args.get("month", current_month_str).strip()

    month_sold_products = []

    daily_total_bills = 0
    daily_qty_sold = 0
    daily_sales_amount = 0.0
    daily_gst_collected = 0.0
    daily_profit = 0.0

    monthly_total_bills = 0
    monthly_total_sales = 0.0
    monthly_qty_sold = 0
    monthly_total_profit = 0.0
    monthly_gst_collected = 0.0

    product_monthly_stats = {}

    for inv in invoices:
        if inv.get("status") in ("Cancelled", "Deleted"):
            continue

        inv_id = str(inv.get("id"))
        try:
            ts = float(inv_id) / 1000.0
            inv_date = time.strftime("%Y-%m-%d", time.localtime(ts))
            inv_display_date = time.strftime("%d/%m/%Y", time.localtime(ts))
            inv_month = time.strftime("%Y-%m", time.localtime(ts))
        except Exception:
            inv_date = today_str
            inv_display_date = time.strftime("%d/%m/%Y")
            inv_month = current_month_str

        items = []
        payment_method = "Cash"
        b_json = inv.get("bill_items", "")
        if b_json:
            try:
                if isinstance(b_json, (dict, list)):
                    parsed_b = b_json
                else:
                    parsed_b = json.loads(b_json)
                if isinstance(parsed_b, dict):
                    payment_method = parsed_b.get("payment_method", "Cash")
                    items = parsed_b.get("items", [])
                elif isinstance(parsed_b, list):
                    items = parsed_b
            except Exception:
                items = []

        is_today = (inv_date == today_str)
        is_target_month = (inv_month == target_month_str)

        if is_today:
            daily_total_bills += 1

        if is_target_month:
            monthly_total_bills += 1

        for item in items:
            name = item.get("name", "").strip()
            qty = int(float(item.get("qty", 0)))
            if not name or qty <= 0:
                continue

            rate = float(item.get("rate", 0))
            gst_str = str(item.get("gst", ""))

            matched_p = prod_map.get(name.lower(), {})
            if rate == 0 and matched_p:
                rate = float(matched_p.get("unit_price") or 0)

            if not gst_str and matched_p:
                gst_str = matched_p.get("gst", "5%")
            elif not gst_str:
                gst_str = "5%"

            try:
                gst_pct = float(gst_str.replace("%", "").strip())
            except Exception:
                gst_pct = 5.0

            cost_price = float(item.get("cost_price", 0))
            if cost_price == 0 and matched_p:
                cost_price = float(matched_p.get("cost_price") or 0)

            subtotal = rate * qty
            gst_val = subtotal * (gst_pct / 100.0)
            total_amt = subtotal + gst_val
            item_profit = (rate - cost_price) * qty

            if is_today:
                daily_qty_sold += qty
                daily_sales_amount += total_amt
                daily_gst_collected += gst_val
                daily_profit += item_profit

            if is_target_month:
                month_sold_products.append({
                    "date": inv_display_date,
                    "product_name": name,
                    "quantity": qty,
                    "selling_price": rate,
                    "gst_pct": f"{gst_pct:g}%",
                    "total_amount": total_amt,
                    "payment_method": payment_method
                })
                monthly_total_sales += total_amt
                monthly_qty_sold += qty
                monthly_gst_collected += gst_val
                monthly_total_profit += item_profit

                if name not in product_monthly_stats:
                    product_monthly_stats[name] = {"name": name, "qty": 0, "profit": 0.0}
                product_monthly_stats[name]["qty"] += qty
                product_monthly_stats[name]["profit"] += item_profit

    product_monthly_sales_list = list(product_monthly_stats.values())
    product_monthly_sales_list.sort(key=lambda x: x["qty"], reverse=True)

    return jsonify({
        "target_month": target_month_str,
        "today_sold_products": month_sold_products,
        "daily_summary": {
            "total_bills": daily_total_bills,
            "total_qty": daily_qty_sold,
            "daily_sales_amount": daily_sales_amount,
            "daily_gst_collected": daily_gst_collected,
            "daily_profit": daily_profit
        },
        "monthly_summary": {
            "total_sales": monthly_total_sales,
            "total_qty": monthly_qty_sold,
            "total_profit": monthly_total_profit,
            "total_gst_collected": monthly_gst_collected,
            "total_bills": monthly_total_bills,
            "total_unique_products": len(product_monthly_stats)
        },
        "product_monthly_sales": product_monthly_sales_list
    })

# =============================
# Start Flask Server
# =============================
def start_flask():
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=False
    )

def open_browser():
    webbrowser.open("http://127.0.0.1:5000/")

if __name__ == "__main__":
    threading.Timer(1.5, open_browser).start()
    app.run(host="127.0.0.1", port=5000, debug=False)