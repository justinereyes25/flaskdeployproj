import os 
import re #password validation gamit regex
import bcrypt #password hashing (secure password storage)
from cryptography.fernet import Fernet #encryption kag decryption sang sensitive data
import mysql.connector

#Flask main modules
from flask import Flask, flash, redirect, render_template, jsonify, request, session, url_for
from datetime import timedelta
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.urandom(24) #secret_key ginagamit para secure ang session data

#Ang key file amo ang ginagamit para ma-store ang encryption key
KEY_FILE = 'secret.key'

if not os.path.exists(KEY_FILE):
    key = Fernet.generate_key()
    with open(KEY_FILE, 'wb') as key_file:
        key_file.write(key)
else:
    with open(KEY_FILE, 'rb') as key_file:
        key = key_file.read()

cipher_suite = Fernet(key)

def get_db_connection():
    return mysql.connector.connect(
        host=os.environ.get("MYSQL_HOST"),
        user=os.environ.get("MYSQL_USER"),
        password=os.environ.get("MYSQL_PASSWORD"),
        database=os.environ.get("MYSQL_DB"),
        port=int(os.environ.get("MYSQL_PORT", 3306))
    )

def encrypt_data(data):
    if data:
        return cipher_suite.encrypt(data.encode()).decode()
    return None

def decrypt_data(encrypted_data):
    if not encrypted_data: return None
    try:
        return cipher_suite.decrypt(encrypted_data.encode()).decode()
    except:
        return encrypted_data

def validate_password(password):
    if len(password) < 8:
        return False
    if not re.search(r"[A-Za-z]", password):
        return False
    if not re.search(r"\d", password):
        return False
    return True

def is_valid_username(username):
    return re.match(r'^[a-zA-Z0-9_.-]+$', username)

def is_valid_email(email):
    return re.match(r'^[^@]+@[^@]+\.[^@]+$', email)

@app.route('/')
def home():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username_input = request.form['username'].strip()
        password_input = request.form['password'].strip()
        
        if not is_valid_username(username_input):
            flash('Invalid username format.', 'error')
            return render_template('login.html')

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT id, username, password_hash, role FROM users")
        all_users = cursor.fetchall()
        conn.close()

        user = None

        for u in all_users:
            decrypted_un = decrypt_data(u['username'])

            #Check kung match sa decrypted OR sa raw (para sa old records)
            if decrypted_un and decrypted_un.strip().lower() == username_input.lower():
                user = u
                break

        if not user:
            flash('User not found', 'error')
            return render_template('login.html')

        try:
            if bcrypt.checkpw(password_input.encode('utf-8'), user['password_hash'].encode('utf-8')):
                session.clear()
                session['user_id'] = user['id']
                session['role'] = user['role']
                session['username'] = decrypt_data(user['username'])
                session.permanent = True
                app.permanent_session_lifetime = timedelta(minutes=30)

                flash('Login successful!', 'success')
                return redirect(url_for('admin_dashboard' if user['role'] == 'admin' else 'user_dashboard'))
            else:
                flash('Invalid password!', 'error')
        except Exception as e:
            flash(f'Login error: {str(e)}', 'error')

    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        email = request.form['email'].strip()
        password = request.form['password'].strip()
        address = request.form['address'].strip()
        role = 'user'

        if not is_valid_username(username):
            flash('Invalid username format.', 'error')
            return redirect(url_for('register'))

        if not is_valid_email(email):
            flash('Invalid email format.', 'error')
            return redirect(url_for('register'))
        
        if not validate_password(password):
            flash('Password must be at least 8 characters with alphanumeric characters.', 'error')
            return redirect(url_for('register'))

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT username, email FROM users")
        all_users = cursor.fetchall()

        for u in all_users:
            dec_un = decrypt_data(u['username'])
            dec_em = decrypt_data(u['email'])
            
            if dec_un and dec_un.lower() == username.lower():
                flash('Username already exists!', 'error')
                conn.close()
                return redirect(url_for('register'))

            if dec_em and dec_em.lower() == email.lower():
                flash('Email already registered!', 'error')
                conn.close()
                return redirect(url_for('register'))

        hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
        
        try:
            cursor.execute(
                "INSERT INTO users (username, email, password_hash, role, address_encrypted) VALUES (%s, %s, %s, %s, %s)",
                (encrypt_data(username), encrypt_data(email), hashed.decode('utf-8'), role, encrypt_data(address))
            )
            conn.commit()
            flash('Registration successful! Please login.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            flash(f"Database error: {e}", "error")
        finally:
            conn.close()

    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    if session['role'] == 'admin':
        return redirect(url_for('admin_dashboard'))
    else:
        return redirect(url_for('user_dashboard'))

@app.route('/dashboard/admin')
def admin_dashboard():
    if 'user_id' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT 
            purchases.id,
            users.username,
            products.name,
            products.price,
            purchases.purchase_date,
            purchases.status
        FROM purchases
        JOIN users ON purchases.user_id = users.id
        JOIN products ON purchases.product_id = products.id
        ORDER BY purchases.purchase_date DESC
    """)
    purchases = cursor.fetchall()
    for p in purchases:
        p['username'] = decrypt_data(p['username'])

    conn.close()
    return render_template('admin_dashboard.html', purchases=purchases)

@app.route('/update_purchase_status_ajax/<int:purchase_id>', methods=['POST'])
def update_purchase_status_ajax(purchase_id):
    if 'user_id' not in session or session.get('role') != 'admin':
        return jsonify({'success': False}), 403

    data = request.get_json()
    new_status = data.get('status')

    if new_status not in ['confirmed', 'pending']:
        return jsonify({'success': False}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE purchases SET status=%s WHERE id=%s",
        (new_status, purchase_id)
    )
    conn.commit()
    conn.close()

    return jsonify({'success': True, 'status': new_status})

@app.route('/dashboard/admin/users', methods=['GET', 'POST'])
def admin_users():
    if session.get('role') != 'admin':
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    if request.method == 'POST' and 'delete_id' in request.form:
        del_id = request.form['delete_id']
        cursor.execute("DELETE FROM users WHERE id=%s", (del_id,))
        conn.commit()
        flash("User deleted.")
        return redirect(url_for('admin_users'))

    cursor.execute("SELECT id, username, role FROM users")
    users = cursor.fetchall()
    for u in users:
        u['username'] = decrypt_data(u['username'])

    conn.close()
    return render_template('admin_users.html', users=users)


@app.route('/dashboard/admin/products', methods=['GET', 'POST'])
def admin_products():
    if session.get('role') != 'admin':
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    if request.method == 'POST':
        name = request.form['name'].strip()
        category = request.form['category']
        try:
            price = float(request.form['price'])
            stock = int(request.form['stock'])
        except ValueError:
            flash("Invalid price or stock value", "error")
            conn.close()
            return redirect(url_for('admin_products'))

        image = request.files.get('image')
        filename = None
        upload_folder = os.path.join('static', 'uploads')
        if not os.path.exists(upload_folder):
            os.makedirs(upload_folder)
        if image and image.filename != "":
            filename = secure_filename(image.filename)
            filepath = os.path.join(upload_folder, filename)
            image.save(filepath)

        if 'add_prod' in request.form:
            cursor.execute(
                "INSERT INTO products (name, price, stock, category, image) VALUES (%s,%s,%s,%s,%s)",
                (name, price, stock, category, filename)
            )
            flash("Product added successfully.", "success")

        elif 'update_prod' in request.form:
            pid = request.form['id']
            if filename:
                cursor.execute(
                    "UPDATE products SET name=%s, price=%s, stock=%s, category=%s, image=%s WHERE id=%s",
                    (name, price, stock, category, filename, pid)
                )
            else:
                cursor.execute(
                    "UPDATE products SET name=%s, price=%s, stock=%s, category=%s WHERE id=%s",
                    (name, price, stock, category, pid)
                )
            flash("Product updated successfully.", "success")

        elif 'delete_prod' in request.form:
            pid = request.form['id']
            cursor.execute("SELECT image FROM products WHERE id=%s", (pid,))
            product = cursor.fetchone()
            if product and product['image']:
                image_path = os.path.join('static/uploads', product['image'])
                if os.path.exists(image_path):
                    os.remove(image_path)
            cursor.execute("DELETE FROM products WHERE id=%s", (pid,))
            flash("Product deleted successfully.", "success")

        conn.commit()
        conn.close()
        return redirect(url_for('admin_products'))

    #Fetch products
    cursor.execute("SELECT * FROM products")
    products = cursor.fetchall()
    conn.close()
    return render_template('admin_products.html', products=products)

@app.route('/dashboard/user')
def user_dashboard():
    if 'user_id' not in session or session.get('role') != 'user':
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, name, price, category, image FROM products")
    products = cursor.fetchall()
    conn.close()
    return render_template('user_dashboard.html', products=products)

@app.route('/dashboard/user/purchases')
def user_products():
    if 'user_id' not in session or session.get('role') != 'user':
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT 
            purchases.id AS purchase_id,
            products.id AS product_id,
            users.username,
            products.name,
            products.price,
            products.category,
            products.image,
            purchases.purchase_date,
            purchases.status,
            purchases.quantity
        FROM purchases
        JOIN users ON purchases.user_id = users.id
        JOIN products ON purchases.product_id = products.id
        WHERE purchases.user_id=%s
        ORDER BY purchases.purchase_date DESC
    """, (session['user_id'],))
    purchased_products = cursor.fetchall()
    conn.close()
    return render_template('user_products.html', purchased_products=purchased_products)

@app.route('/update_purchase_qty', methods=['POST'])
def update_purchase_qty():
    data = request.get_json()
    p_id = data.get('purchase_id')
    new_qty = data.get('quantity')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE purchases SET quantity=%s WHERE id=%s", (new_qty, p_id))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})

@app.route('/buy/<int:product_id>')
def buy_product(product_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT username FROM users WHERE id=%s", (session['user_id'],))
    user = cursor.fetchone()

    cursor.execute("SELECT id, name, price, category FROM products WHERE id=%s", (product_id,))
    product = cursor.fetchone()

    if not product:
        flash("Product not found", "error")
        conn.close()
        return redirect(url_for('user_dashboard'))

    cursor.execute(
        "INSERT INTO purchases (user_id, product_id, customer_name, product_name, price, category, quantity, status, purchase_date) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,'pending',NOW())",
        (session['user_id'], product['id'], user['username'], product['name'], product['price'], product['category'], 1)
    )
    conn.commit()
    conn.close()
    flash("Product purchased successfully!", "success")
    return redirect(url_for('user_dashboard'))


@app.route('/buy_again/<int:product_id>', methods=['POST'])
def buy_again(product_id):
    if 'user_id' not in session:
        return {"success": False, "message": "You must be logged in."}, 401

    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        #Check if the product exists
        cursor.execute("SELECT price FROM products WHERE id = %s", (product_id,))
        product = cursor.fetchone()
        if not product:
            return {"success": False, "message": "Product not found."}, 404

        cursor.execute(
            "SELECT id, quantity FROM purchases WHERE user_id = %s AND product_id = %s AND status != 'cancelled'",
            (user_id, product_id)
        )
        purchase = cursor.fetchone()

        if purchase:
            new_quantity = purchase['quantity'] + 1
            cursor.execute(
                "UPDATE purchases SET quantity = %s, purchase_date = NOW() WHERE id = %s",
                (new_quantity, purchase['id'])
            )
        else:
            #New purchase
            cursor.execute(
                "INSERT INTO purchases (user_id, product_id, purchase_date, status, quantity) VALUES (%s, %s, NOW(), 'pending', 1)",
                (user_id, product_id)
            )

        conn.commit()
        return {"success": True, "message": "Product added successfully!"}

    except Exception as e:
        print("Buy Again Error:", e)
        return {"success": False, "message": "Failed to add product."}, 500

    finally:
        conn.close()

@app.route('/dashboard/user/purchases/cancel/<int:purchase_id>', methods=['POST'])
def cancel_purchase(purchase_id):

    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        "DELETE FROM purchases WHERE id=%s AND user_id=%s",
        (purchase_id, session['user_id'])
    )

    conn.commit()
    conn.close()

    flash("Purchase canceled successfully", "warning")

    return redirect(url_for('user_products'))

@app.route('/dashboard/user/profile', methods=['GET', 'POST'])
def user_profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        address = request.form.get('address', '').strip()
        new_password = request.form.get('password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()

        if not re.match(r'^[a-zA-Z0-9_.-]+$', username):
            flash("Invalid username format!", "error")
            return redirect(url_for('user_profile'))

        if not re.match(r'^[^@]+@[^@]+\.[^@]+$', email):
            flash("Invalid email format!", "error")
            return redirect(url_for('user_profile'))

        #1.Check Duplicates (I-decrypt anay ang DB records antes i-compare)
        cursor.execute("SELECT id, username, email FROM users WHERE id != %s", (session['user_id'],))
        for u in cursor.fetchall():
            if decrypt_data(u['username']).lower() == username.lower():
                flash("Username already taken.", "error")
                conn.close()
                return redirect(url_for('user_profile'))
            
            if decrypt_data(u['email']).lower() == email.lower():
                flash("Email already used by another account.", "error")
                conn.close()
                return redirect(url_for('user_profile'))

        #2.Encrypt all PII data
        user_enc = encrypt_data(username)
        email_enc = encrypt_data(email)
        address_enc = encrypt_data(address)

        #3.Update query
        if new_password:
            if new_password != confirm_password:
                flash("Passwords do not match.", "error")
                return redirect(url_for('user_profile'))
            
            if not validate_password(new_password):
                flash("Password must be at least 8 characters with letters and numbers.", "error")
                return redirect(url_for('user_profile'))

            hashed_password = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt())

            cursor.execute(
                "UPDATE users SET username=%s, email=%s, address_encrypted=%s, password_hash=%s WHERE id=%s",
                (user_enc, email_enc, address_enc, hashed_password.decode('utf-8'), session['user_id'])
            )
        else:
            cursor.execute(
                "UPDATE users SET username=%s, email=%s, address_encrypted=%s WHERE id=%s",
                (user_enc, email_enc, address_enc, session['user_id'])
            )

        conn.commit()
        conn.close()

        session['username'] = username
        flash("Profile updated successfully.", "success")

        return redirect(url_for('user_profile'))

    cursor.execute("SELECT username, email, address_encrypted FROM users WHERE id=%s", (session['user_id'],))
    user = cursor.fetchone()
    if user:
        user['username'] = decrypt_data(user['username'])
        user['email'] = decrypt_data(user['email'])
        user['address_encrypted'] = decrypt_data(user['address_encrypted'])
    
    conn.close()
    return render_template('user_profile.html', user=user)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)