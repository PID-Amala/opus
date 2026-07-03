import httpx
from opus_escrow.config import get_settings

settings = get_settings()

async def send_whatsapp_message(to_number: str, message: str):
    """The low-level API call to Meta."""
    # Ensure number doesn't have a '+' or leading zeros if necessary
    clean_number = to_number.strip().replace("+", "")
    
    url = f"https://graph.facebook.com/v21.0/{settings.whatsapp_phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {settings.whatsapp_token}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": clean_number,
        "type": "text",
        "text": {"body": message}
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, headers=headers)
        return response.json()