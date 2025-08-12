# Déploiement sur Render

## Étapes :
1. Pousser ce projet sur un dépôt GitHub/GitLab.
2. Aller sur [Render](https://render.com) → New Web Service → connecter votre dépôt.
3. Choisir **Python 3.10+**.
4. Dans "Start Command", Render utilisera automatiquement :
    gunicorn webhook_chariow_v1.app:app
5. Créer un disque persistant :
   - Nom : `data`
   - Point de montage : `/opt/data`
6. Dans les variables d'environnement Render, définir :
   - DB_PATH=/opt/data/database.sqlite
   - WHATSAPP_INSTANCE_ID=...
   - WHATSAPP_TOKEN=...
   - GOOGLE_SHEETS_CREDENTIALS=...
   - GOOGLE_SHEET_ID=...
   - WEBHOOK_SECRET=...
   - SENDER_EMAIL=...
7. Déployer et tester votre application.
