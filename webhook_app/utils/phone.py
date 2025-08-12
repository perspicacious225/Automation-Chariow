import re
from typing import Optional

class PhoneNormalizer:
    RULES = {
        "CI": {"code": "225", "prefix": "0", "length": 10},
        "BJ": {"code": "229", "prefix": "0", "length": 10},
        "CM": {"code": "237", "prefix": "6", "length": 9}
    }

    @classmethod
    def normalize(cls, phone: str) -> Optional[str]:
        digits = re.sub(r"\D", "", phone)
        # print(digits)
        
        if digits.startswith("00"):
            digits = digits[2:]
            
        for rules in cls.RULES.values():
            if digits.startswith(rules["code"] + rules["prefix"]) and rules["prefix"] =="0":
                return f"{rules['code']}{digits[len(rules['code']):]}@c.us"
            
            elif digits.startswith(rules["code"] + rules["prefix"]) and rules["prefix"] =="6":
                return f"{rules["code"]}{digits[len(rules["code"])+1:]}@c.us"

            elif len(digits) == rules["length"] and digits.startswith(rules["prefix"]):
                return f"{rules['code']}{digits[1:]}@c.us"
            
            # if digits.startswith(rules["code"] + rules[])
        
                
        return None
    
# test = PhoneNormalizer()
# print(test.normalize("+237689333213"))