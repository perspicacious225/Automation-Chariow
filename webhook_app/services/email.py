import os
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import base64
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from config import Config

logger = logging.getLogger(__name__)

class EmailService:
    def __init__(self):
        self.SCOPES = ['https://www.googleapis.com/auth/gmail.send']
        self.creds = None
        self.service = None
        self._authenticate()

    def _authenticate(self):
        """Méthode d'authentification corrigée"""
        if os.path.exists('token.json'):
            self.creds = Credentials.from_authorized_user_file('token.json', self.SCOPES)
        
        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                self.creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    'credentials_gmail.json',
                    self.SCOPES,
                    redirect_uri='urn:ietf:wg:oauth:2.0:oob'
                )
                auth_url, _ = flow.authorization_url(prompt='consent')
                print(f"Ouvrez ce lien dans votre navigateur : {auth_url}")
                code = input("Entrez le code d'autorisation : ")
                token_response = flow.fetch_token(code=code) 
                self.creds = flow.credentials  # Récupération  des credentials
            
            with open('token.json', 'w') as token:
                token.write(self.creds.to_json())

        self.service = build('gmail', 'v1', credentials=self.creds)

    def send_email(self, recipient, subject, body):
        """Envoi d'email via Gmail API"""
        try:
            message = MIMEMultipart()
            message['From'] = Config.SENDER_EMAIL
            message['To'] = recipient
            message['Subject'] = subject
            message.attach(MIMEText(body, 'html'))
            # message.attach(MIMEText(body, 'plain'))
            
            raw_message = {
                'raw': base64.urlsafe_b64encode(message.as_bytes()).decode()
            }
            
            self.service.users().messages().send( # type: ignore
                userId='me',
                body=raw_message
            ).execute()
            
            logger.info(f"Email envoyé à {recipient}")
            return True
            
        except Exception as e:
            logger.error(f"Erreur d'envoi : {str(e)}")
            return False