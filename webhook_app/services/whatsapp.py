import re
import requests
import logging
from typing import Optional
from webhook_app.config import Config

logger = logging.getLogger(__name__)

class WhatsAppService:
    COUNTRY_RULES = {
        "CI": {"code": "225", "prefix": "0", "length": 10},
        "BJ": {"code": "229", "prefix": "0", "length": 10},
        "CM": {"code": "237", "prefix": "6", "length": 9}
    }

    @classmethod
    def _normalize_digits(cls, phone: str) -> Optional[str]:
        """
        Reprend EXACTEMENT la même logique que normalize_phone, mais retourne
        uniquement les chiffres au format international (sans '@c.us').
        """
        digits = re.sub(r"\D", "", phone or "")
        if digits.startswith("00"):
            digits = digits[2:]

        for rules in cls.COUNTRY_RULES.values():
            if digits.startswith(rules["code"] + rules["prefix"]) and rules["prefix"] == "0":
                # enlève 'code' + '0' puis recolle 'code'
                digits = rules["code"] + digits[len(rules["code"]) + 2:]
                break
            elif digits.startswith(rules["code"] + rules["prefix"]) and rules["prefix"] == "6":
                digits = rules["code"] + digits[len(rules["code"]) + 1:]
                break
            elif len(digits) == rules["length"] and digits.startswith(rules["prefix"]):
                digits = rules["code"] + digits[2:]
                break

        return digits or None
    
    @classmethod
    def normalize_for_dedupe(cls, phone: str) -> Optional[str]:
        """
        Version pour la DÉDUPLICATION: retourne uniquement les chiffres
        avec indicatif (ex: '22507xxxxxxx'), SANS '@c.us'.
        """
        return cls._normalize_digits(phone)

    # OPTIONNEL : si tu veux, tu peux aussi réécrire normalize_phone pour réutiliser _normalize_digits:
    @classmethod
    def normalize_phone(cls, phone: str) -> Optional[str]:
        digits = cls._normalize_digits(phone)
        return f"{digits}@c.us" if digits else None

    def send_message(self, phone: str, message: str) -> bool:
        chat_id = self.normalize_phone(phone)
        if not chat_id:
            logger.error(f"Invalid phone: {phone}")
            return False

        try:
            response = requests.post(
                Config.API_URL,
                json={"chatId": chat_id, "message": message},
                timeout=10
            )
            response.raise_for_status()
            logger.info(f"Message sent to {chat_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to send to {chat_id}: {str(e)}")
            return False

# test =  WhatsAppService()

# print(test.normalize_for_dedupe("2250789333113"))