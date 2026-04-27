import os
import uuid
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ========== IP HELPERS ==========
def get_client_ip():
    """Lấy IP thật của user"""
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if ip and ',' in ip:
        ip = ip.split(',')[0].strip()
    return ip or 'unknown'

def get_country_from_ip(ip):
    """Lấy quốc gia từ IP"""
    if ip in ('127.0.0.1', 'localhost', '::1', 'unknown'):
        return 'Local'
    try:
        resp = requests.get(f'http://ip-api.com/json/{ip}?fields=country', timeout=3)
        if resp.status_code == 200:
            return resp.json().get('country', 'unknown')
    except:
        pass
    return 'unknown'

# ========== HELPERS ==========
def create_user_if_not_exists(telegram_id, username, first_name, referred_by=None, country='unknown', register_ip='unknown'):
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
            "balance": 0,
            "country": country,
            "register_ip": register_ip,
            "last_ad_ip": register_ip
        }
        supabase.table("users").insert(new_user).execute()
        
        if referred_by:
            referrer = supabase.table("users").select("*").eq("ref_code", referred_by).execute()
            if referrer.data:
                ref_user = referrer.data[0]
                supabase.table("users").update({
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

@app.route('/api/adsgram-reward', methods=['GET'])
def adsgram_reward():
    """Server-to-server callback từ Adsgram xác nhận user đã xem ads"""
    user_id = request.args.get('userid')
    reward_amount = request.args.get('reward', 0)
    
    print(f"✅ Adsgram SERVER verified: user={user_id}, reward={reward_amount}")
    
    # Lưu xác nhận từ Adsgram server vào database
    try:
        supabase.table("ad_verifications").insert({
            "telegram_id": user_id,
            "verified_at": "now()",
            "verified_by": "adsgram_server",
            "reward": int(reward_amount) if reward_amount else 0
        }).execute()
    except Exception as e:
        print(f"Error saving verification: {e}")
    
    return jsonify({"status": "ok", "message": "Reward verified by server"})

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
    ip = get_client_ip()
    country = get_country_from_ip(ip)
    
    user = create_user_if_not_exists(
        str(data.get('telegram_id')),
        data.get('username', ''),
        data.get('first_name', 'Unknown'),
        data.get('referred_by'),
        country,
        ip
    )
    if user:
        return jsonify({'message': 'User ready', 'user': user})
    return jsonify({'error': 'Cannot create user'}), 500

@app.route('/api/user/update-balance', methods=['POST'])
def update_balance():
    data = request.json
    telegram_id = str(data.get('telegram_id'))
    reward = int(data.get('reward', 0))
    ip = get_client_ip()
    
    try:
        user = supabase.table("users").select("*").eq("telegram_id", telegram_id).execute().data[0]
        supabase.table("users").update({
            "balance": user['balance'] + reward,
            "total_earned": user['total_earned'] + reward,
            "ads_watched": user['ads_watched'] + 1,
            "last_claim": "now()",
            "last_ad_ip": ip,
            "last_ad_time": "now()"
        }).eq("telegram_id", telegram_id).execute()
        
        supabase.table("ad_claims").insert({"telegram_id": telegram_id, "reward": reward}).execute()
        
        # Lưu xác nhận từ frontend
        supabase.table("ad_verifications").insert({
            "telegram_id": telegram_id,
            "verified_at": "now()",
            "verified_by": "frontend",
            "reward": reward
        }).execute()
        
        # Thưởng 10% cho người giới thiệu
        if user.get('referred_by'):
            referrer = supabase.table("users").select("*").eq("ref_code", user['referred_by']).execute()
            if referrer.data:
                ref_user = referrer.data[0]
                bonus = int(reward * 0.1)
                if bonus > 0:
                    supabase.table("users").update({
                        "balance": ref_user['balance'] + bonus,
                        "ref_earnings": ref_user['ref_earnings'] + bonus
                    }).eq("ref_code", user['referred_by']).execute()
        
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
        pending = supabase.table("withdrawals").select("*").eq("status", "pending").execute()
        
        pending_list = []
        for w in pending.data:
            user_info = supabase.table("users").select("username").eq("telegram_id", w['telegram_id']).execute()
            username = user_info.data[0]['username'] if user_info.data else 'unknown'
            pending_list.append({**w, 'username': username})
        
        stats = {
            'total_users': len(users.data),
            'pending_count': len(pending.data)
        }
        
        return jsonify({
            "users": len(users.data),
            "pending": pending_list,
            "stats": stats
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/user/<telegram_id>/referrals', methods=['GET'])
def get_referrals(telegram_id):
    try:
        user = supabase.table("users").select("ref_code").eq("telegram_id", telegram_id).execute()
        if not user.data:
            return jsonify([])
        
        ref_code = user.data[0]['ref_code']
        referrals = supabase.table("users").select(
            "first_name, total_earned, created_at"
        ).eq("referred_by", ref_code).order("created_at", desc=True).execute()
        
        return jsonify(referrals.data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/user/<telegram_id>/ad-verifications', methods=['GET'])
def get_ad_verifications(telegram_id):
    """Kiểm tra lịch sử xác nhận ads của user"""
    try:
        verifications = supabase.table("ad_verifications").select("*").eq("telegram_id", telegram_id).order("verified_at", desc=True).limit(50).execute()
        return jsonify(verifications.data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
