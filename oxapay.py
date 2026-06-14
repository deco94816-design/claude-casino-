import aiohttp
import hmac
import hashlib

class OxaPay:
    def __init__(self, merchant_key):
        self.merchant_key = merchant_key
        self.base_url = "https://api.oxapay.com"

    async def create_deposit_address(self, coin, network, user_id):
        url = f"{self.base_url}/merchants/request/whitelabel"
        payload = {
            "merchant": self.merchant_key,
            "amount": 0.10,
            "currency": "USD",
            "payCurrency": coin,
            "network": network,
            "orderId": str(user_id),
            "lifeTime": 60,
            "description": f"Deposit for user {user_id}"
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                try:
                    data = await resp.json()
                except aiohttp.ContentTypeError:
                    print(f"OxaPay API Error: Non-JSON response (Status: {resp.status})")
                    return None
                
                if data.get("result") == 100:
                    return {
                        "address": data.get("address"),
                        "network": data.get("network"),
                        "trackId": str(data.get("trackId"))
                    }
                else:
                    print(f"OxaPay API Error: {data}", flush=True)
                    return {"error": data.get("message", "API Error")}

    @staticmethod
    def verify_webhook_signature(payload_str, signature, merchant_key):
        calculated_sig = hmac.new(
            merchant_key.encode('utf-8'),
            payload_str.encode('utf-8'),
            hashlib.sha512
        ).hexdigest()
        return hmac.compare_digest(calculated_sig, signature)

    async def get_supported_coins(self):
        url = f"{self.base_url}/api/networks"
        payload = {"merchant": self.merchant_key}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                try:
                    data = await resp.json()
                    return data
                except aiohttp.ContentTypeError:
                    print(f"OxaPay API Error: Non-JSON response (Status: {resp.status})")
                    return {}

    async def inquiry_deposit(self, trackId):
        url = f"{self.base_url}/merchants/inquiry"
        payload = {
            "merchant": self.merchant_key,
            "trackId": trackId
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                try:
                    data = await resp.json()
                    return data
                except aiohttp.ContentTypeError:
                    print(f"OxaPay API Error: Non-JSON response (Status: {resp.status})")
                    return {}
