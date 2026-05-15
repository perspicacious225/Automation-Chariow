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
        digits = re.sub(r"\D", "", phone or "")
        if digits.startswith("00"):
            digits = digits[2:]

        for rules in cls.COUNTRY_RULES.values():
            if digits.startswith(rules["code"] + rules["prefix"]) and rules["prefix"] == "0":
                # CI : 2250XXXXXXXX (12 chiffres) → déjà normalisé, ne pas toucher
                expected_full_length = len(rules["code"]) + 1 + (rules["length"] - 2)
                if len(digits) == 12 and rules["code"] == "225":
                    break  # déjà au bon format
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


    @classmethod
    def normalize_phone(cls, phone: str) -> Optional[str]:
        digits = cls._normalize_digits(phone)
        return f"{digits}@c.us" if digits else None

    def send_message(self, phone: str, message: str) -> bool:
        digits = self._normalize_digits(phone)
        if not digits:
            logger.error(f"Invalid phone: {phone}")
            return False

        # 1. Chercher le LID en cache depuis conversations
        chat_id = self._get_cached_lid(digits)

        # 2. Si pas de cache → résoudre via CheckWhatsapp
        if not chat_id:
            lid = self._call_check_whatsapp(digits)
            chat_id = lid if lid else f"{digits}@c.us"

        try:
            response = requests.post(
                Config.API_URL,
                json={"chatId": chat_id, "message": message},
                timeout=10
            )
            logger.info(f"Green API status: {response.status_code} | chatId: {chat_id}")
            response.raise_for_status()
            logger.info(f"Message sent to {chat_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to send to {chat_id}: {str(e)}")
            return False


    def _get_cached_lid(self, digits: str) -> Optional[str]:
        """Cherche le LID dans conversations via le numéro normalisé."""
        try:
            # from webhook_app.database_conv import get_connection, execute_with_retry
            from webhook_app.database_pg import get_connection as pg_get_connection, execute_with_retry as pg_exec
            with pg_get_connection(readonly=True) as conn:
                row = pg_exec(
                    conn,
                    """
                    SELECT lid FROM conversations
                    WHERE phone LIKE %s AND lid IS NOT NULL
                    LIMIT 1
                    """,
                    (f"%{digits}%",),
                    fetch="one",
                )
                if row and row.get("lid"):
                    logger.debug(f"LID trouvé en cache pour {digits}: {row['lid']}")
                    return row["lid"]
        except Exception as e:
            logger.debug(f"Cache LID non disponible pour {digits}: {e}")
        return None
        

    
        
    def resolve_chat_id(self, phone_raw: str, conv_id: str | None = None) -> Optional[str]:
        """
        Convertit un chatId @c.us en LID valide via CheckWhatsapp.
        Si conv_id fourni, utilise le cache DB pour éviter les appels répétés.
        """
        logger.info("resolve_chat_id — phone=%s | conv_id=%s", phone_raw, conv_id)
        # Vérifier le cache DB d'abord
        if conv_id:
            from webhook_app.database_conv import get_or_set_lid
            return get_or_set_lid(
                conv_id=conv_id,
                phone_raw=phone_raw,
                resolver_fn=self._call_check_whatsapp,
            )
        

        # Sans conv_id — appel direct
        return self._call_check_whatsapp(phone_raw)


    def _call_check_whatsapp(self, phone_raw: str) -> Optional[str]:
        """Appel API CheckWhatsapp — logique isolée pour réutilisation."""
        digits = phone_raw.replace("@c.us", "").strip()
        try:
            url = f"https://api.green-api.com/waInstance{Config.INSTANCE_ID}/checkWhatsapp/{Config.TOKEN}"
            response = requests.post(
                url,
                json={"phoneNumber": digits},
                timeout=10
            )
            data = response.json()
            if data.get("existsWhatsapp") and data.get("chatId"):
                logger.info(f"LID résolu : {digits} → {data['chatId']}")
                return data["chatId"]
        except Exception as e:
            logger.error(f"resolve_chat_id failed for {digits}: {e}")
        return None


    def send_message_direct(self, chatId: str, message: str, conv_id: str | None = None) -> bool:
        """Envoi avec résolution LID automatique."""
        # Résoudre le vrai chatId via CheckWhatsapp
        resolved = self.resolve_chat_id(chatId, conv_id=conv_id)
        final_id = resolved or chatId
        
        try:
            response = requests.post(
                Config.API_URL,
                json={"chatId": final_id, "message": message},
                timeout=10
            )
            logger.info(f"Green API status: {response.status_code} | chatId: {final_id}")
            logger.info(f"Green API body: {response.text}")
            response.raise_for_status()
            logger.info(f"Message sent to {final_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to send to {final_id}: {str(e)}")
            return False


    def delete_message(self, chat_id: str, message_id: str) -> bool:
        """
        Supprime un message dans une conversation WhatsApp.
        Utilisé pour supprimer les tags admin (#REPRISE etc.)
        après traitement — invisibles pour le client.
        """
        try:
            url = (
                f"https://api.green-api.com"
                f"/waInstance{Config.INSTANCE_ID}"
                f"/deleteMessage/{Config.TOKEN}"
            )
            response = requests.post(
                url,
                json={"chatId": chat_id, "idMessage": message_id},
                timeout=10,
            )
            response.raise_for_status()
            logger.info(f"Message supprimé : {message_id} dans {chat_id}")
            return True
        except Exception as e:
            logger.error(f"Erreur suppression message {message_id} : {e}")
            return False


    def send_to_admin(self, message: str, conv_id: str | None = None) -> bool:
        admin_phone = Config.ADMIN_PHONE
        if not admin_phone:
            logger.warning("ADMIN_PHONE non configuré")
            return False

        # Utiliser le LID résolu depuis la conversation si disponible
        if conv_id:
            from webhook_app.database_conv import get_or_set_lid
            chat_id = get_or_set_lid(
                conv_id=conv_id,
                phone_raw=f"{admin_phone}@c.us",
                resolver_fn=self._call_check_whatsapp,
            )
        else:
            lid = self._call_check_whatsapp(admin_phone)
            chat_id = lid if lid else f"{admin_phone}@c.us"

        try:
            response = requests.post(
                Config.API_URL,
                json={"chatId": chat_id, "message": message},
                timeout=10,
            )
            response.raise_for_status()
            logger.info(f"Notification admin envoyée → {chat_id}")
            return True
        except Exception as e:
            logger.error(f"Erreur notification admin : {e}")
            return False
# test =  WhatsAppService()

# print(test.normalize_for_dedupe("2250789333113"))