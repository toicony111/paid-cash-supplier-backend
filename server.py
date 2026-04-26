import os
import uuid
from flask import Flask, request, jsonify
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# Khởi tạo Supabase client
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") # Dùng service_role cho server
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ========== HELPERS ==========
def create_user_if_not_exists(telegram_id, username, first_name, referred_by=None):
    """Tìm user, nếu chưa có thì tạo mới"""
    try:
        user = supabase.table("users").select("*").eq("telegram_id", telegram_id).execute()
        if user.data:
            return user.data[0]
        
        own_ref = uuid.uuid4().hex[:8]
        new_user = {
            "telegram_id": telegram_id,
            "username": username,
            "first_name": first_name,
            "ref_code": own_ref,
            "referred_by": referred_by,
            "balance": 0
        }
        supabase.table("users").insert(new_user).execute()
        
        # Thưởng cho người giới thiệu
        if referred_by:
            referrer = supabase.table("users").select("*").eq("ref_code", referred_by).execute()
            if referrer.data:
                ref_user = referrer.data[0]
                supabase.table("users").update({
                    "balance": ref_user['balance'] + 5000,
                    "ref_earnings": ref_user['ref_earnings'] + 5000,
                    "ref_count": ref_user['ref_count'] + 1
                }).eq("ref_code", referred_by).execute()
        
        return supabase.table("users").select("*").eq("telegram_id", telegram_id).execute().data[0]
    except Exception as e:
        print(f"Error in create_user_if_not_exists: {e}")
        return None

# ========== API ENDPOINTS ==========

@app.route('/')
def home():
    return jsonify({"status": "ok", "message": "Paid Cash Supplier API is running on Supabase"})

@app.route('/api/user/<telegram_id>', methods=['GET'])
def get_user(telegram_id):
    try:
        user = supabase.table("users").select("*").eq("telegram_id", telegram_id).execute()
        if user.data:
            return jsonify(user.data[0])
        return jsonify({'error': 'User not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/user/create', methods=['POST'])
def create_user():
    data = request.json
    user = create_user_if_not_exists(
        str(data.get('telegram_id')),
        data.get('username', ''),
        data.get('first_name', 'Unknown'),
        data.get('referred_by')
    )
    if user:
        return jsonify({'message': 'User ready', 'user': user})
    return jsonify({'error': 'Cannot create user'}), 500

@app.route('/api/user/update-balance', methods=['POST'])
def update_balance():
    data = request.json
    telegram_id = str(data.get('telegram_id'))
    reward = int(data.get('reward', 0))
    
    try:
        user = supabase.table("users").select("*").eq("telegram_id", telegram_id).execute().data[0]
        supabase.table("users").update({
            "balance": user['balance'] + reward,
            "total_earned": user['total_earned'] + reward,
            "ads_watched": user['ads_watched'] + 1,
            "last_claim": "now()"
        }).eq("telegram_id", telegram_id).execute()
        
        supabase.table("ad_claims").insert({"telegram_id": telegram_id, "reward": reward}).execute()
        
        return jsonify({'balance': user['balance'] + reward, 'reward': reward})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/withdraw', methods=['POST'])
def withdraw():
    data = request.json
    telegram_id = str(data.get('telegram_id'))
    method = data.get('method')
    wallet_address = data.get('wallet_address')
    amount = int(data.get('amount', 0))
    
    try:
        user = supabase.table("users").select("*").eq("telegram_id", telegram_id).execute().data[0]
        if amount < 100000: return jsonify({'error': 'Min 100,000 coins'}), 400
        if user['balance'] < amount: return jsonify({'error': 'Not enough coins'}), 400
        
        supabase.table("users").update({"balance": user['balance'] - amount}).eq("telegram_id", telegram_id).execute()
        supabase.table("withdrawals").insert({
            "telegram_id": telegram_id, "method": method,
            "wallet_address": wallet_address, "amount": amount, "status": "pending"
        }).execute()
        
        return jsonify({'message': 'Withdrawal submitted', 'new_balance': user['balance'] - amount})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/withdrawals/<telegram_id>', methods=['GET'])
def get_withdrawals(telegram_id):
    try:
        withdrawals = supabase.table("withdrawals").select("*").eq("telegram_id", telegram_id).order("created_at", desc=True).limit(20).execute()
        return jsonify(withdrawals.data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/admin')
def admin_panel():
    try:
        users = supabase.table("users").select("*").order("balance", desc=True).limit(50).execute()
        pending = supabase.table("withdrawals").select("*, users(username)").eq("status", "pending").execute()
        stats = {
            'total_users': len(users.data),
            'pending_count': len(pending.data)
        }
        # Tạo HTML tương tự như trước...
        return jsonify({"users": len(users.data), "pending": len(pending.data), "stats": stats})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
