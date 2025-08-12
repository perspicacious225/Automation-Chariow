import gspread
from oauth2client.service_account import ServiceAccountCredentials
import logging
from config import Config

logger = logging.getLogger(__name__)

class GoogleSheetsService:
    SCOPE = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]

    def __init__(self):
        self.creds = ServiceAccountCredentials.from_json_keyfile_name(
            Config.CREDS_PATH, self.SCOPE) # type: ignore
        self.client = gspread.authorize(self.creds) # type: ignore
        self.sheet = self.client.open_by_key(Config.SHEET_ID).sheet1 # type: ignore

    def append_sale(self, sale) -> bool:
        try:
            row = [
                sale.customer_name,         # NomDuClient
                sale.customer_country,      # Pays
                sale.customer_phone,        # Telephone
                sale.customer_email,        # Email
                sale.product_name,          # Produit
                sale.amount,                # Prix
                sale.store_url,             # Lien (url de la boutique)
                sale.status,                 # Status
                sale.created_at,             # Date de création de la commande
                sale.checkout_url,           # UrlCheckout
                sale.store_name             # Boutique

            ]
            self.sheet.append_row(row)
            logger.info("Sale added to sheet")
            return True
        except Exception as e:
            logger.error(f"Sheet error: {str(e)}")
            return False