from flask import Flask, request, jsonify
from flask_cors import CORS
import sqlite3
import hashlib
import hmac
import time
import os

app = Flask(__name__)
CORS(app)

BOT_TOKEN = "YOUR_BOT_TOKEN"  # Token bot Telegram của bạn
DB_NAME = "database.db"

# ========== DATABASE SETUP ==========
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Bảng users
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id TEXT UNIQUE NOT NULL,
        username TEXT,
        first_name TEXT,
        balance INTEGER DEFAULT 0,
        total_earned INTEGER DEFAULT 0,
        ads_watched INTEGER DEFAULT 0,
        ref_code TEXT UNIQUE,
        referred_by TEXT,
        ref_earnings INTEGER DEFAULT 0,
        ref_count INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_banned INTEGER DEFAULT 0,
        last_claim TIMESTAMP
    )''')
    
    # Bảng withdrawals
    c.execute('''CREATE TABLE IF NOT EXISTS withdrawals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id TEXT NOT NULL,
        method TEXT NOT NULL,
        wallet_address TEXT NOT NULL,
        amount INTEGER NOT NULL,
        status TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        processed_at TIMESTAMP
    )''')
    
    # Bảng ad_claims (log mỗi lần xem ad)
    c.execute('''CREATE TABLE IF NOT EXISTS ad_claims (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id TEXT NOT NULL,
        reward INTEGER NOT NULL,
        claimed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # Bảng admin
    c.execute('''CREATE TABLE IF NOT EXISTS admins (
        telegram_id TEXT PRIMARY KEY,
        role TEXT DEFAULT 'admin'
    )''')
    
    conn.commit()
    conn.close()

init_db()

# ========== HELPERS ==========
def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def verify_telegram_data(init_data):
    """Verify Telegram initData từ Mini App"""
    try:
        # Tách các tham số
        data_dict = {}
        for item in init_data.split('&'):
            if '=' in item:
                key, value = item.split('=', 1)
                data_dict[key] = value
        
        # Lấy hash từ data
        received_hash = data_dict.pop('hash', '')
        
        # Sắp xếp và tạo data_check_string
        sorted_items = sorted(data_dict.items())
        data_check_string = '\n'.join([f"{k}={v}" for k, v in sorted_items])
        
        # Tạo secret key từ bot token
        secret_key = hmac.new(
            key=b"WebAppData",
            msg=BOT_TOKEN.encode(),
            digestmod=hashlib.sha256
        ).digest()
        
        # Tạo hash để so sánh
        calculated_hash = hmac.new(
            key=secret_key,
            msg=data_check_string.encode(),
            digestmod=hashlib.sha256
        ).hexdigest()
        
        return calculated_hash == received_hash
    except:
        return False

# ========== API ENDPOINTS ==========

@app.route('/api/user/<telegram_id>', methods=['GET'])
def get_user(telegram_id):
    """Lấy thông tin user"""
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE telegram_id = ?', (telegram_id,)).fetchone()
    db.close()
    
    if user:
        return jsonify({
            'telegram_id': user['telegram_id'],
            'username': user['username'],
            'first_name': user['first_name'],
            'balance': user['balance'],
            'total_earned': user['total_earned'],
            'ads_watched': user['ads_watched'],
            'ref_code': user['ref_code'],
            'ref_count': user['ref_count'],
            'ref_earnings': user['ref_earnings'],
            'is_banned': user['is_banned'],
            'created_at': user['created_at']
        })
    return jsonify({'error': 'User not found'}), 404

@app.route('/api/user/create', methods=['POST'])
def create_user():
    """Tạo user mới khi vào Mini App lần đầu"""
    data = request.json
    telegram_id = str(data.get('telegram_id'))
    username = data.get('username', '')
    first_name = data.get('first_name', 'Unknown')
    ref_code = data.get('ref_code', '')  # Mã ref từ người giới thiệu
    referred_by = data.get('referred_by', None)
    
    db = get_db()
    
    # Kiểm tra user đã tồn tại chưa
    existing = db.execute('SELECT * FROM users WHERE telegram_id = ?', (telegram_id,)).fetchone()
    if existing:
        db.close()
        return jsonify({'message': 'User already exists', 'user': {
            'telegram_id': existing['telegram_id'],
            'balance': existing['balance']
        }})
    
    # Tạo ref code riêng cho user
    import uuid
    own_ref = uuid.uuid4().hex[:8]
    
    db.execute('''INSERT INTO users (telegram_id, username, first_name, balance, ref_code, referred_by)
                  VALUES (?, ?, ?, 0, ?, ?)''',
               (telegram_id, username, first_name, own_ref, referred_by))
    
    # Nếu có referred_by, cập nhật cho người giới thiệu
    if referred_by:
        referrer = db.execute('SELECT * FROM users WHERE ref_code = ?', (referred_by,)).fetchone()
        if referrer:
            db.execute('UPDATE users SET ref_count = ref_count + 1 WHERE ref_code = ?', (referred_by,))
            # Bonus 5000 coin cho người mời
            db.execute('UPDATE users SET balance = balance + 5000, ref_earnings = ref_earnings + 5000 WHERE ref_code = ?', (referred_by,))
    
    db.commit()
    db.close()
    
    return jsonify({'message': 'User created', 'ref_code': own_ref}), 201

@app.route('/api/user/update-balance', methods=['POST'])
def update_balance():
    """Cập nhật số dư sau khi xem ad"""
    data = request.json
    telegram_id = str(data.get('telegram_id'))
    reward = int(data.get('reward', 0))
    
    db = get_db()
    
    user = db.execute('SELECT * FROM users WHERE telegram_id = ?', (telegram_id,)).fetchone()
    if not user:
        db.close()
        return jsonify({'error': 'User not found'}), 404
    
    if user['is_banned']:
        db.close()
        return jsonify({'error': 'User is banned'}), 403
    
    # Cập nhật balance
    new_balance = user['balance'] + reward
    db.execute('''UPDATE users 
                  SET balance = ?, 
                      total_earned = total_earned + ?, 
                      ads_watched = ads_watched + 1,
                      last_claim = CURRENT_TIMESTAMP
                  WHERE telegram_id = ?''',
               (new_balance, reward, telegram_id))
    
    # Log claim
    db.execute('INSERT INTO ad_claims (telegram_id, reward) VALUES (?, ?)',
               (telegram_id, reward))
    
    db.commit()
    db.close()
    
    return jsonify({'balance': new_balance, 'reward': reward})

@app.route('/api/withdraw', methods=['POST'])
def withdraw():
    """Tạo lệnh rút tiền"""
    data = request.json
    telegram_id = str(data.get('telegram_id'))
    method = data.get('method')
    wallet_address = data.get('wallet_address')
    amount = int(data.get('amount', 0))
    
    db = get_db()
    
    user = db.execute('SELECT * FROM users WHERE telegram_id = ?', (telegram_id,)).fetchone()
    if not user:
        db.close()
        return jsonify({'error': 'User not found'}), 404
    
    if user['is_banned']:
        db.close()
        return jsonify({'error': 'User is banned'}), 403
    
    if amount < 100000:
        db.close()
        return jsonify({'error': 'Minimum withdraw is 100,000 coins'}), 400
    
    if user['balance'] < amount:
        db.close()
        return jsonify({'error': 'Insufficient balance'}), 400
    
    # Trừ balance
    new_balance = user['balance'] - amount
    db.execute('UPDATE users SET balance = ? WHERE telegram_id = ?', (new_balance, telegram_id))
    
    # Tạo lệnh rút
    db.execute('''INSERT INTO withdrawals (telegram_id, method, wallet_address, amount, status)
                  VALUES (?, ?, ?, ?, 'pending')''',
               (telegram_id, method, wallet_address, amount))
    
    db.commit()
    withdrawal_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
    db.close()
    
    return jsonify({
        'message': 'Withdrawal submitted',
        'withdrawal_id': withdrawal_id,
        'new_balance': new_balance
    })

@app.route('/api/withdrawals/<telegram_id>', methods=['GET'])
def get_withdrawals(telegram_id):
    """Lấy lịch sử rút tiền"""
    db = get_db()
    withdrawals = db.execute(
        'SELECT * FROM withdrawals WHERE telegram_id = ? ORDER BY created_at DESC LIMIT 20',
        (telegram_id,)
    ).fetchall()
    db.close()
    
    return jsonify([{
        'id': w['id'],
        'method': w['method'],
        'wallet': w['wallet_address'][:10] + '...',
        'amount': w['amount'],
        'status': w['status'],
        'created_at': w['created_at']
    } for w in withdrawals])

@app.route('/api/admin/stats', methods=['GET'])
def admin_stats():
    """Thống kê cho admin"""
    db = get_db()
    
    total_users = db.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    total_withdrawals = db.execute('SELECT SUM(amount) FROM withdrawals WHERE status = "completed"').fetchone()[0] or 0
    pending_withdrawals = db.execute('SELECT COUNT(*) FROM withdrawals WHERE status = "pending"').fetchone()[0]
    
    db.close()
    
    return jsonify({
        'total_users': total_users,
        'total_paid': total_withdrawals,
        'pending_withdrawals': pending_withdrawals
    })

@app.route('/api/admin/pending-withdrawals', methods=['GET'])
def pending_withdrawals():
    """Danh sách lệnh rút đang chờ"""
    db = get_db()
    withdrawals = db.execute('''
        SELECT w.*, u.username, u.first_name 
        FROM withdrawals w 
        JOIN users u ON w.telegram_id = u.telegram_id 
        WHERE w.status = 'pending' 
        ORDER BY w.created_at DESC
    ''').fetchall()
    db.close()
    
    return jsonify([{
        'id': w['id'],
        'telegram_id': w['telegram_id'],
        'username': w['username'],
        'first_name': w['first_name'],
        'method': w['method'],
        'wallet': w['wallet_address'],
        'amount': w['amount'],
        'created_at': w['created_at']
    } for w in withdrawals])

@app.route('/api/admin/approve-withdrawal', methods=['POST'])
def approve_withdrawal():
    """Admin duyệt lệnh rút"""
    data = request.json
    withdrawal_id = data.get('withdrawal_id')
    
    db = get_db()
    db.execute('UPDATE withdrawals SET status = "completed", processed_at = CURRENT_TIMESTAMP WHERE id = ?',
               (withdrawal_id,))
    db.commit()
    db.close()
    
    return jsonify({'message': 'Withdrawal approved'})

# ========== ADMIN PANEL (đơn giản) ==========
@app.route('/admin')
def admin_panel():
    db = get_db()
    users = db.execute('SELECT * FROM users ORDER BY balance DESC LIMIT 50').fetchall()
    pending = db.execute('''
        SELECT w.*, u.username FROM withdrawals w 
        JOIN users u ON w.telegram_id = u.telegram_id 
        WHERE w.status = 'pending'
    ''').fetchall()
    stats = {
        'total_users': db.execute('SELECT COUNT(*) FROM users').fetchone()[0],
        'total_paid': db.execute('SELECT SUM(amount) FROM withdrawals WHERE status="completed"').fetchone()[0] or 0,
        'pending_count': db.execute('SELECT COUNT(*) FROM withdrawals WHERE status="pending"').fetchone()[0]
    }
    db.close()
    
    html = f'''
    <html><head><title>Admin Panel</title>
    <style>body{{font-family:Arial;padding:20px;background:#f5f5f5}}
    table{{border-collapse:collapse;width:100%;background:white;margin:10px 0}}
    th,td{{border:1px solid #ddd;padding:8px;text-align:left}}
    th{{background:#4CAF50;color:white}}
    .btn{{padding:5px 15px;background:#4CAF50;color:white;border:none;border-radius:4px;cursor:pointer}}
    .card{{background:white;padding:15px;border-radius:8px;margin:10px 0;box-shadow:0 2px 4px rgba(0,0,0,0.1)}}
    </style></head><body>
    <h1>📊 Admin Panel</h1>
    <div class="card">
        <h3>Stats</h3>
        <p>Total Users: {stats['total_users']}</p>
        <p>Total Paid: {stats['total_paid']:,} coins</p>
        <p>Pending Withdrawals: {stats['pending_count']}</p>
    </div>
    <h2>Pending Withdrawals</h2>
    <table><tr><th>ID</th><th>User</th><th>Method</th><th>Wallet</th><th>Amount</th><th>Action</th></tr>'''
    
    for w in pending:
        html += f'''<tr>
            <td>{w['id']}</td><td>@{w['username']}</td>
            <td>{w['method']}</td><td>{w['wallet_address'][:15]}...</td>
            <td>{w['amount']:,}</td>
            <td><a href="/admin/approve/{w['id']}" style="color:green">Approve</a></td>
        </tr>'''
    
    html += '</table></body></html>'
    return html

@app.route('/admin/approve/<int:withdrawal_id>')
def approve_web(withdrawal_id):
    db = get_db()
    db.execute('UPDATE withdrawals SET status = "completed", processed_at = CURRENT_TIMESTAMP WHERE id = ?',
               (withdrawal_id,))
    db.commit()
    db.close()
    return '<script>alert("Approved!");window.location="/admin"</script>'

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
