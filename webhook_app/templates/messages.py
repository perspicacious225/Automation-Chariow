    # Templates (simplified)
TEMPLATES_WHATSAPP = {
        "success":
"""
✅ *Paiement confirmé ! Votre licence Office 365 est prête* ✅

Bonjour {name},

Félicitations ! Votre paiement de *{amount} FCFA* pour *{product_name}* a bien été reçu.

*📦 Comment accéder à votre licence :*
1. Allez sur : portal.chariow.com
2. Entrez votre email : *{customer_email}*
3. Validez avec le code de vérification (envoyé par email)

⏱ *Le code arrive généralement immédiatement* - Vérifiez vos spams si besoin.

*🛠 Besoin d'aide ?*
Contactez notre support :
- WhatsApp : Laissez nous un message
- Email : contact.digitetchub@gmail.com

*🔍 Découvrez aussi :*
- Pack Formation Excel Avancé : {store_url}/excel-formation
- Tous nos produits : {store_url}

Merci pour votre confiance !
L'équipe {store_name}

📌 *Référence* : CMD-{sale_id}
""",

        "failure": 
"""
Bonjour {name}, 

Oups! Il semble y avoir eu un souci avec votre paiement pour *{product_name}*.

Votre accès à *{product_name}* ({amount}) est bloqué par un paiement interrompu. Causes fréquentes : 
• Problème réseau 
• Plafond carte dépassé 
• Session expirée

Pas de panique, votre panier est sauvegardé.

🛠️ *Solutions rapides :* 
1. Réessayez avec Wave/Orange Money/carte/MTN MONEY ici :
{checkout_url} 
2. Besoin d'aide ? Nous sommes à votre écoute !

💎 *Offre sécurisée :* 
✅ Licence à vie Office 365 + Windows 11 Pro 
✅ Guide d'installation 5 min (vidéo incluse) 
✅ Support WhatsApp illimité 
✅ Remboursement 24h si insatisfait

⏰ *Offre exclusive valable 24h* (après : {product_value} FCFA)

L'équipe {store_name} vous attend !
""",
        "abandon":
"""
Bonjour {name},

Nous avons remarqué que votre commande pour *{product_name}* n'a pas pu être finalisée, soit suite à un abandon accidentel, soit à une erreur de paiement.

Pas de panique, votre panier est sauvegadé.

Ne passez pas à côté de :
✅ Offre exclusive : {amount} FCFA au lieu de {product_value} FCFA
✅ Licence à vie 100% légale
✅ Installation facile en 5 min (vidéo incluse)
✅ Garantie satisfait ou remboursé 24h

🔗 Reprenez votre paiement ici : 
{checkout_url} 

⏰ *Offre valable 24h seulement !* 

Un problème ? Nous sommes à votre écoute ! 
Laissez-nous un message ici.

À tout de suite, 
— L'équipe {store_name}
"""
    }

EMAIL_TEMPLATES = {
    "success": """
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{
            font-family: 'Segoe UI', Arial, sans-serif;
            line-height: 1.6;
            color: #2d2d2d;
            max-width: 650px;
            margin: 0 auto;
            padding: 0;
            background-color: #f5f5f5;
        }}
        .header {{
            background: linear-gradient(135deg, #107C10 0%, #004B1C 100%);
            color: white;
            padding: 30px 20px;
            text-align: center;
            border-radius: 8px 8px 0 0;
        }}
        .container {{
            background: white;
            padding: 0;
            border-radius: 0 0 8px 8px;
            box-shadow: 0 3px 10px rgba(0,0,0,0.1);
        }}
        .content {{
            padding: 25px;
        }}
        .success-badge {{
            background-color: #E6F3D7;
            color: #107C10;
            padding: 15px;
            border-radius: 6px;
            margin: 20px 0;
            text-align: center;
            font-weight: bold;
        }}
        .access-card {{
            background-color: #F3F9FB;
            border: 1px dashed #0078D4;
            padding: 20px;
            margin: 25px 0;
            border-radius: 6px;
        }}
        .button {{
            background: linear-gradient(135deg, #107C10 0%, #004B1C 100%);
            color: white;
            padding: 14px 30px;
            text-decoration: none;
            border-radius: 6px;
            display: inline-block;
            font-weight: bold;
            font-size: 16px;
            margin: 15px 0;
            text-align: center;
        }}
        .whatsapp-badge {{
            display: inline-block;
            background-color: #25D366;
            color: white;
            padding: 8px 15px;
            border-radius: 20px;
            margin: 10px 0;
            font-size: 14px;
        }}
        .product-grid {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 15px;
            margin: 20px 0;
        }}
        .product-card {{
            border: 1px solid #E1E1E1;
            border-radius: 6px;
            padding: 10px;
            text-align: center;
            font-size: 13px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>✅ Paiement confirmé ! Votre commande est prête</h1>
        </div>
        
        <div class="content">
            <p>Bonjour {name},</p>
            
            <div class="success-badge">
                Merci pour votre achat de {product_name} pour {amount} FCFA
            </div>
            
            <div class="access-card">
                <h3 style="margin-top: 0;">📦 Accédez à votre licence</h3>
                <p>Pour récupérer votre produit :</p>
                <ol>
                    <li>Allez sur <a href="https://portal.chariow.com" target="_blank">portal.chariow.com</a></li>
                    <li>Entrez votre adresse email <strong>{customer_email}</strong></li>
                    <li>Validez avec le code de vérification envoyé à cet email</li>
                </ol>
                
                <center>
                    <a href="https://portal.chariow.com" class="button">Accéder au portail maintenant</a>
                </center>
                
                <p style="font-size: 13px; color: #666;">
                    <em>Le code de vérification arrive généralement immédiatement.</em>
                </p>
            </div>
            
            <h3>Besoin d'aide ?</h3>
            <p>Notre équipe est disponible sur :</p>
            <div class="whatsapp-badge">
                Cliquez sur <a href="wa.me/2250576654850">WhatsApp</a>
            </div>
            
            <h3>Découvrez aussi :</h3>
            <div class="product-grid">
                <div class="product-card">
                    <strong>Windows 11 Pro</strong><br>
                    Licence à vie - 7 000 FCFA
                </div>
                <div class="product-card">
                    <strong>Formation Excel</strong><br>
                    Pack complet - 10 000 FCFA
                </div>
            </div>
            
            <p style="text-align: center; margin-top: 25px;">
                <a href="{store_url}" style="color: #0078D4;">👉 Voir tous nos produits</a>
            </p>
        </div>
        
        <div class="footer">
            <p>{store_name} © {current_year} | <a href="{store_url}" style="color: #0078D4;">Notre boutique</a></p>
            <p style="font-size: 11px;">Référence de commande : {sale_id}</p>
        </div>
    </div>
</body>
</html>
    """,
    "failure": """
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{
            font-family: 'Segoe UI', Arial, sans-serif;
            line-height: 1.6;
            color: #2d2d2d;
            max-width: 650px;
            margin: 0 auto;
            padding: 0;
            background-color: #f5f5f5;
        }}
        .header {{
            background: linear-gradient(135deg, #D83B01 0%, #A80000 100%);
            color: white;
            padding: 30px 20px;
            text-align: center;
        }}
        .container {{
            background: white;
            padding: 0;
            border-radius: 0 0 8px 8px;
            box-shadow: 0 3px 10px rgba(0,0,0,0.1);
        }}
        .content {{
            padding: 25px;
        }}
        .alert-banner {{
            background-color: #FEF0F1;
            border-left: 4px solid #A80000;
            padding: 15px;
            margin: 20px 0;
        }}
        .solution-card {{
            background-color: #F3F9FB;
            border-left: 4px solid #0078D4;
            padding: 15px;
            margin: 20px 0;
        }}
        .button {{
            background: linear-gradient(135deg, #D83B01 0%, #A80000 100%);
            color: white;
            padding: 14px 30px;
            text-decoration: none;
            border-radius: 6px;
            display: inline-block;
            font-weight: bold;
            font-size: 16px;
            margin: 15px 0;
            text-align: center;
        }}
        .payment-methods {{
            display: flex;
            justify-content: center;
            gap: 15px;
            margin: 20px 0;
            flex-wrap: wrap;
        }}
        .payment-method {{
            background: #F3F2F1;
            padding: 10px 15px;
            border-radius: 4px;
            font-size: 14px;
        }}
        .step {{
            display: flex;
            margin-bottom: 15px;
            align-items: flex-start;
        }}
        .step-number {{
            background-color: #0078D4;
            color: white;
            width: 24px;
            height: 24px;
            border-radius: 50%;
            display: flex;
            justify-content: center;
            align-items: center;
            margin-right: 10px;
            flex-shrink: 0;
            font-size: 14px;
        }}
        .guarantee-badge {{
            display: inline-block;
            background: #E6F3D7;
            color: #107C10;
            padding: 8px 15px;
            border-radius: 4px;
            margin: 10px 0;
            font-size: 14px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Action requise : Votre paiement a échoué</h1>
        </div>
        
        <div class="content">
            <p>Bonjour {name},</p>
            
            <div class="alert-banner">
                ❗ <strong>Nous avons détecté un problème</strong> avec votre paiement pour Microsoft Office 365 à vie.
            </div>
            
            <h3>3 solutions simples pour finaliser votre achat :</h3>
            
            <div class="step">
                <div class="step-number">1</div>
                <div>
                    <strong>Réessayer avec la même carte</strong><br>
                    L'erreur peut être temporaire. Cliquez simplement ci-dessous :
                </div>
            </div>
            
            <center>
                <a href="{checkout_url}" class="button" style="color: white;">⟳ Réessayer le paiement</a>
            </center>
            
            <div class="step">
                <div class="step-number">2</div>
                <div>
                    <strong>Changer de mode de paiement</strong><br>
                    Essayez avec l'un de ces moyens alternatifs :
                </div>
            </div>
            
            <div class="payment-methods">
                <span class="payment-method">Wave</span>
                <span class="payment-method">Orange Money</span>
                <span class="payment-method">Airtel Money</span>
                <span class="payment-method">Carte bancaire ...</span>
            </div>
            
            <div class="step">
                <div class="step-number">3</div>
                <div>
                    <strong>Contacter notre support</strong><br>
                    Cliquez sur : <a href="wa.me/2250576654850">WhatsApp</a><br>
                    Email: {support_email}
                </div>
            </div>
            
            <div class="solution-card">
                <h3 style="margin-top: 0;">Pourquoi ça arrive ?</h3>
                <ul>
                    <li>Problème technique temporaire</li>
                    <li>Fonds insuffisants sur le compte</li>
                    <li>Limite de transaction...</li>
                </ul>
            </div>
            
            <div class="guarantee-badge">
                ⏳ Votre commande est réservée pour 4h - Prix garanti : {amount} FCFA
            </div>
            
            <p><strong>Vos avantages préservés :</strong></p>
            <ul>
                <li>Licence à vie officielle Microsoft Office 365</li>
                <li>Licence à vie officielle Windows 10 & 11 pro</li>
                <li>Tutoriel d'installation en 5 min</li>
                <li>Support personnalisé jusqu'à activation</li>
            </ul>
        </div>
        <center>
                <a href="{checkout_url}" class="button" style="color: white;">⟳ Réessayer le paiement maintenant</a>
        </center>
        
        <div class="footer">
            <p>{store_name} © {current_year} | <a href="{store_url}" style="color: #0078D4;">Notre boutique</a></p>
        </div>
    </div>
</body>
</html>
    """,
    "abandon": """
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{
            font-family: 'Segoe UI', Arial, sans-serif;
            line-height: 1.6;
            color: #2d2d2d;
            max-width: 650px;
            margin: 0 auto;
            padding: 0;
            background-color: #f5f5f5;
        }}
        .header {{
            background: linear-gradient(135deg, #0078D4 0%, #004E8C 100%);
            color: white;
            padding: 30px 20px;
            text-align: center;
        }}
        .container {{
            background: white;
            padding: 0;
            border-radius: 0 0 8px 8px;
            box-shadow: 0 3px 10px rgba(0,0,0,0.1);
        }}
        .content {{
            padding: 25px;
        }}
        .urgency-banner {{
            background-color: #FFF4E5;
            border-left: 4px solid #FF8C00;
            padding: 15px;
            margin: 20px 0;
            font-size: 15px;
        }}
        .button {{
            background: linear-gradient(135deg, #FF8C00 0%, #F7630C 100%);
            color: white;
            padding: 14px 30px;
            text-decoration: none;
            border-radius: 6px;
            display: inline-block;
            font-weight: bold;
            font-size: 16px;
            margin: 15px 0;
            text-align: center;
        }}
        .benefit-item {{
            margin-bottom: 12px;
            padding-left: 25px;
            position: relative;
        }}
        .benefit-item:before {{
            content: "✓";
            color: #107C10;
            font-weight: bold;
            position: absolute;
            left: 0;
        }}
        .warning-item {{
            margin-bottom: 12px;
            padding-left: 25px;
            position: relative;
            color: #A80000;
        }}
        .warning-item:before {{
            content: "!";
            color: #A80000;
            font-weight: bold;
            position: absolute;
            left: 0;
        }}
        .price-comparison {{
            display: flex;
            justify-content: center;
            margin: 25px 0;
        }}
        .old-price {{
            text-decoration: line-through;
            color: #666;
            margin-right: 15px;
        }}
        .new-price {{
            font-weight: bold;
            color: #A80000;
            font-size: 1.2em;
        }}
        .footer {{
            background-color: #F3F2F1;
            padding: 20px;
            text-align: center;
            font-size: 12px;
            color: #666;
            border-radius: 0 0 8px 8px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Votre licence Office 365 à vie vous attend !</h1>
        </div>
        
        <div class="content">
            <p>Bonjour {name},</p>
            
            <div class="urgency-banner">
                ⏳ <strong>DERNIÈRE CHANCE</strong> - Votre offre spéciale expire dans 4 heures
            </div>
            
            <p>Votre commande pour <strong>Microsoft Office 365 à vie</strong> n'a pas pu être finalisée. Pas de panique ! Voici comment compléter votre achat en 2 minutes :</p>
            
            <ul>
                <h3>Ce que vous risquez à ne pas terminer :</h3>
                <li><div class="warning-item">Bloquer vos fichiers au pire moment</div></li>
                <li><div class="warning-item">Continuer à payer des abonnements mensuels</div></li>
                <li><div class="warning-item">Vous exposer aux logiciels piratés et virus</div></li>
            </ul>
            
            <div class="price-comparison">
                <span class="old-price">{product_value} FCFA</span>
                <span class="new-price">{amount} FCFA (-70%)</span>
            </div>
            
            <center>
                <a href="{checkout_url}" class="button">👉 Finaliser ma commande maintenant</a>
            </center>
            
            <ul>
                <h3>Vos avantages exclusifs :</h3>
                <li><div class="benefit-item">Licence à vie 100% légale pour Office 365 + Windows 11 Pro</div></li>
                <li><div class="benefit-item">Guide d'installation pas-à-pas en vidéo (5 min)</div></li>
                <li><div class="benefit-item">Support WhatsApp/e-mail jusqu'à activation</div></li>
                <li><div class="benefit-item">Garantie satisfait ou remboursé 48h</div></li>
            </ul>
            
            <p style="font-size: 14px; color: #666; margin-top: 30px;">
                <strong>Modes de paiement acceptés :</strong> Wave, Orange Money, Airtel Money, cartes bancaires...<br>
                <strong>Support :</strong> Cliquez sur <a href="wa.me/2250576654850">WhatsApp </a> ou réponse à cet e-mail
            </p>
        </div>
        
        <div class="footer">
            <p>{store_name} © {current_year} | <a href="{store_url}" style="color: #0078D4;">Notre boutique</a></p>
            <p style="font-size: 11px;">Cette offre est réservée exclusivement à {name} et expire dans 4h </p>
        </div>
    </div>
</body>
</html>
    """
}

EMAIL_SUBJECTS = {
"success": "Confirmation de commande -",
"failure": "Paiement échoué -",
"abandon": "Votre panier vous attend -"
}
