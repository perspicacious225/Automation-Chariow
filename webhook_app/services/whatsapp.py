import re
import requests
import logging
from typing import Optional
from config import Config

logger = logging.getLogger(__name__)

class WhatsAppService:
    COUNTRY_RULES = {
        "CI": {"code": "225", "prefix": "0", "length": 10},
        "BJ": {"code": "229", "prefix": "0", "length": 10},
        "CM": {"code": "237", "prefix": "6", "length": 9}
    }

    @classmethod
    def normalize_phone(cls, phone: str) -> Optional[str]:
        digits = re.sub(r"\D", "", phone)
        # print("qwert", digits)
        if digits.startswith("00"):
            digits = digits[2:]
            
        for rules in cls.COUNTRY_RULES.values():
            if digits.startswith(rules["code"] + rules["prefix"]) and rules["prefix"] == "0":
                digits = rules["code"] + digits[len(rules["code"]) + 2:]
                # print("qwert2", digits)
                break
            elif digits.startswith(rules["code"] + rules["prefix"]) and rules["prefix"] == "6":
                 digits = rules["code"] + digits[len(rules["code"]) + 1:]
                #  print(digits)
                 break
            elif len(digits) == rules["length"] and digits.startswith(rules["prefix"]):
                digits = rules["code"] + digits[2:]
                break
                
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

# print(test.normalize_phone("237689333113"))