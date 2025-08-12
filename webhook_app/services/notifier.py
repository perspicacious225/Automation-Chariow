# from ..config import Config
from .email import EmailService, to_plain
from .whatsapp import WhatsAppService
from templates import messages
import logging
from webhook_app.config import Config
from webhook_app.models.sale import Sale

logger = logging.getLogger(__name__)

class Notifier:
    def __init__(self):
        self.email_service = EmailService()
        self.whatsapp_service = WhatsAppService()

    def _prepare_template_vars(self, sale: Sale) -> dict:
        """Prépare les variables communes à tous les templates"""
        return {
            'name': sale.customer_name or "Client",
            'product_name': sale.product_name or "votre produit",
            'store_name': sale.store_name or "Notre Boutique",
            'customer_email': sale.customer_email or "",
            'checkout_url': sale.checkout_url or "#",
            'store_url': sale.store_url or "#",
            'amount': sale.amount or "",
            'product_value': getattr(sale, 'product_value', ""),  # Plus sûr que direct access
            'current_year': sale.current_year or "2023",
            'sale_id': sale.id,
            'support_email': Config.SENDER_EMAIL
        }
    

    def _send_notification(self, sale: Sale, template_type: str):
        """Méthode privée pour centraliser l'envoi des notifications"""
        try:
            template_vars = self._prepare_template_vars(sale)
            # WhatsApp
            whatsapp_msg = messages.TEMPLATES_WHATSAPP[template_type].format(**template_vars)
            self.whatsapp_service.send_message(sale.customer_phone, whatsapp_msg)
    
            # Email
            email_body = messages.EMAIL_TEMPLATES[template_type].format(**template_vars)
            self.email_service.send_email(
            recipient=sale.customer_email,
            subject=f"🔔 {messages.EMAIL_SUBJECTS[template_type]} {sale.store_name}",
            html_body=email_body,
            plain_fallback=to_plain(email_body))


            logger.info(f"Notifications {template_type} envoyées pour {sale.id}")
            return True
            
        except Exception as e:
            logger.error(f"Erreur notifications {template_type}: {str(e)}")
            return False

    def handle_abandoned(self, sale: Sale):
        """Panier abandonné"""
        return self._send_notification(sale, "abandon")

    def handle_failed(self, sale: Sale):
        """Paiement échoué"""
        return self._send_notification(sale, "failure")

    def handle_success(self, sale: Sale):
        """Paiement réussi"""
        return self._send_notification(sale, "success")