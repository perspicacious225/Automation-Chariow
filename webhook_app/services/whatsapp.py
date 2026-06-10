import re
import requests
import logging
from typing import Optional
from webhook_app.config import Config

logger = logging.getLogger(__name__)


import phonenumbers
class WhatsAppService:


    @classmethod
    def _normalize_digits(cls, phone: str, country: str = "CI") -> Optional[str]:
        """
        Normalise vers E.164 sans le + via libphonenumber (Google).
        Gère automatiquement tous les formats et changements pays.
        """
        if not phone:
            return None

        # Nettoyer suffixes WhatsApp et caractères non-numériques
        clean = re.sub(r'@.*$', '', phone).strip()
        clean = re.sub(r'\D', '', clean)
        if not clean:
            return None

        #1 — numéro international
        try:
            parsed = phonenumbers.parse(f"+{clean}")
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(
                    parsed, phonenumbers.PhoneNumberFormat.E164
                ).lstrip("+")
        except phonenumbers.NumberParseException:
            pass

        # 2 — numéro local + country_code connu
    
        try:
            parsed = phonenumbers.parse(clean, country)
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(
                    parsed, phonenumbers.PhoneNumberFormat.E164
                ).lstrip("+")
        except Exception:
            pass


        # retourner les chiffres bruts (mieux que None)
        logger.warning("normalize_digits — numéro non reconnu : %s", phone)
        return clean or None
    
    @classmethod
    def normalize_for_dedupe(cls, phone: str, country: str = "CI") -> Optional[str]:
        """
        Version pour la DÉDUPLICATION: retourne uniquement les chiffres
        avec indicatif (ex: '22507xxxxxxx'), SANS '@c.us'.
        """
        return cls._normalize_digits(phone, country=country)


    @classmethod
    def normalize_phone(cls, phone: str, country: str = "CI") -> Optional[str]:
        digits = cls._normalize_digits(phone, country=country)
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

    @staticmethod
    def calculate_delays(
        incoming: str,
        outgoing: str,
        reading_wps: float = 6.0,
        typing_wps: float = 0.30,
    ) -> tuple[float, float]:
        reading_words = max(1, len(incoming.split()))
        typing_words  = max(1, len(outgoing.split()))

        reading_delay = min(max(1, reading_words / reading_wps), 6.0)
        typing_delay  = min(max(1.0, typing_words  / typing_wps),  30.0)

        return round(reading_delay, 1), round(typing_delay, 1)

    def _send_typing_action(self, chat_id: str, typing_time_ms: int = 10000) -> None:
        try:
            url = (
                f"https://api.green-api.com"
                f"/waInstance{Config.INSTANCE_ID}"
                f"/sendTyping/{Config.TOKEN}"
            )
            response = requests.post(
                url,
                json={"chatId": chat_id, "typingTime": typing_time_ms},
                timeout=5,
            )
            logger.debug(
                "sendTyping — status=%d | chatId=%s | typingTime=%dms",
                response.status_code, chat_id, typing_time_ms,
            )
        except Exception as e:
            logger.debug("sendTyping échoué (non bloquant) : %s", e)


    def _send_typing_action_sustained(self, chat_id: str, duration: float, interval: float = 15.0) -> None:
        """
        Maintient l'indicateur 'en train d'écrire' actif pendant toute la durée.
        Renvoie SendTyping toutes les `interval` secondes.
        
        interval : 10s
        """
        import time
        elapsed = 0.0
        while elapsed < duration:
            remaining = duration - elapsed
            
            typing_time_ms = min(int(min(interval, remaining) * 1000), 20000)
            self._send_typing_action(chat_id, typing_time_ms=typing_time_ms)
            sleep_time = min(interval - 1.0, remaining)
            time.sleep(sleep_time)
            elapsed += sleep_time


    def send_message_direct(
        self,
        chatId: str,
        message: str,
        conv_id: str | None = None,
        reading_delay: float = 0.0,   
        typing_delay:  float = 0.0,   
        ) -> bool:

        """Envoi avec résolution LID automatique + retry sur timeout."""
        import time

        resolved = self.resolve_chat_id(chatId, conv_id=conv_id)
        final_id = resolved or chatId

        import threading

        if reading_delay > 0:
            time.sleep(reading_delay)

        if typing_delay > 0:
            # Typing action en background
            def _typing_bg():
                self._send_typing_action_sustained(final_id, duration=typing_delay)

            t = threading.Thread(target=_typing_bg, daemon=True)
            t.start()
            time.sleep(typing_delay)   
        for attempt in range(3):
            try:
                response = requests.post(
                    Config.API_URL,
                    json={"chatId": final_id, "message": message},
                    timeout=10
                )
                logger.info(f"Green API status: {response.status_code} | chatId: {final_id}")
                response.raise_for_status()
                return True

            except requests.exceptions.Timeout:
                wait = 2 ** (attempt +1)  
                logger.warning(
                    "Green API timeout (tentative %d/3) — retry dans %ds | chatId: %s",
                    attempt + 1, wait, final_id
                )
                if attempt < 2:
                    time.sleep(wait)
                    continue
                logger.error(
                    "Green API : échec après 3 tentatives (timeout) | chatId: %s",
                    final_id
                )
                return False

            except requests.exceptions.ConnectionError as e:
                logger.error(f"Green API connexion impossible : {str(e)}")
                return False

            except Exception as e:
                logger.error(f"Failed to send to {final_id}: {str(e)}")
                return False

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