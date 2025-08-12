#!/usr/bin/env python3
import os
import time
import json
import requests
import subprocess
import logging
from threading import Thread
from pathlib import Path

# Configuration 
BASE_DIR = Path(__file__).parent
FLASK_APP = "webhook_app.app:create_app()"  # Point d'entrée de l'application
NGROK_PORT = 5000
TEST_INTERVAL =  300  # secondes
PAYLOAD_PATH = BASE_DIR / "webhook_app/templates/last_webhook.json"

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(BASE_DIR / 'webhook_app/logs/webhook_test.log'),
        logging.StreamHandler()
    ]
)

class WebhookTester:
    def __init__(self):
        self.ngrok_url = None
        self.processes = []

    def start_service(self, cmd):
        """Démarre un service en arrière-plan"""
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=BASE_DIR
        )
        self.processes.append(proc)
        return proc

    def start_flask(self):
        """Démarre l'application Flask"""
        os.environ['FLASK_APP'] = FLASK_APP
        self.start_service(["flask", "run", f"--port={NGROK_PORT}"])
        logging.info("Serveur Flask démarré")

    def start_ngrok(self):
        """Démarre Ngrok et récupère l'URL publique"""
        self.start_service(["ngrok", "http", str(NGROK_PORT)])
        time.sleep(3)  # Attente pour Ngrok
        
        try:
            res = requests.get("http://localhost:4040/api/tunnels", timeout=5)
            self.ngrok_url = res.json()['tunnels'][0]['public_url']
            logging.info(f"URL Ngrok active: {self.ngrok_url}")
        except Exception as e:
            logging.error(f"Erreur Ngrok: {str(e)}")
            raise

    def load_payload(self):
        """Charge le payload de test"""
        try:
            with open(PAYLOAD_PATH) as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Erreur payload: {str(e)}")
            raise

    def send_test_webhook(self):
        """Envoie un test de webhook"""
        try:
            payload = self.load_payload()
            response = requests.post(
                f"{self.ngrok_url}/webhook",
                json=payload,
                headers={'Content-Type': 'application/json'},
                timeout=10
            )
            logging.info(f"Test réussi (HTTP {response.status_code})")
        except Exception as e:
            logging.error(f"Échec du test: {str(e)}")

    def cleanup(self):
        """Nettoyage des processus"""
        for proc in self.processes:
            proc.terminate()
        logging.info("Processus arrêtés")

    def run(self):
        """Exécution principale"""
        try:
            self.start_flask()
            self.start_ngrok()
            
            while True:
                self.send_test_webhook()
                time.sleep(TEST_INTERVAL)
                
        except KeyboardInterrupt:
            self.cleanup()
        except Exception as e:
            logging.error(f"Erreur critique: {str(e)}")
            self.cleanup()
            raise

if __name__ == "__main__":
    tester = WebhookTester()
    tester.run()