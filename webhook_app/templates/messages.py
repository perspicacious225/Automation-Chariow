# ==============================================================================
# 1. BLOC DE CONTENU COMMUN (VOTRE VERSION STATIQUE ORIGINALE)
# ==============================================================================
# C'est votre bloc HTML exact, prêt à être inséré dans les emails de confirmation.
Message_commun_html = """
<div style="background-color:#F3F9FB;border:1px dashed #0078D4;padding:16px;border-radius:6px;margin:20px 0;">
  <p>Pour accéder dès maintenant à votre produit et aux bonus, voici la marche à suivre :</p>
  <p>1. Cliquez sur ce lien :
    <a href="https://portal.chariow.com" style="color:#0078D4;text-decoration:underline;word-break:break-all;">https://portal.chariow.com</a>
  </p>
  <p>2. Entrez l'email que vous avez utilisé pour votre achat : <strong>{customer_email}</strong></p>
  <p>3. Copiez le code que vous recevrez par email et collez-le sur le site.<br>
  Vous aurez alors accès instantanément à vos achats.</p>
  <hr style="border:none;border-top:1px solid #ddd;margin:20px 0;">
  <p>Si vous rencontrez la moindre difficulté, je suis là. Répondez simplement à cet email ou contactez-moi sur WhatsApp :
    <a href="https://wa.me/2250576654850" style="color:#0078D4;text-decoration:underline;">+225 05 76 65 48 50</a>.
    C'est moi, Yanick, qui vous assisterai personnellement.
  </p>
  <p>À très vite,<br><strong>Yanick Kouame</strong><br>Fondateur, Digitech Hub</p>
</div>
"""

# Message WhatsApp de confirmation (votre version statique originale)
Message_commun_wa = (
    "📦 *Comment accéder à votre produit :*\n"
    "1. Allez sur : portal.chariow.com\n"
    "2. Entrez votre email : {customer_email}\n"
    "3. Validez avec le code reçu par mail.\n\n"
    "Si vous avez besoin d'aide, je suis là ! 😉\n"
    "-Yanick"
)

# ==============================================================================
# 2. SUJETS D'EMAIL (CLÉS ORIGINALES CONSERVÉES)
# ==============================================================================
EMAIL_SUBJECTS = {
    "success": "Confirmation de votre commande : {product_name}",
    "failure": "Paiement échoué pour votre commande",
    "abandon": "Votre panier vous attend - {product_name}",
    "relance_t30":  "Un souci avec votre commande ?",
    "relance_t6h":  "{customer_first_name}, concernant votre commande sur Digitech Hub",
    "relance_t23h": "Dernier rappel : votre offre spéciale se termine bientôt",
    "relance_t47h": "Fermeture de votre offre spéciale ce soir",
    "confirm_3_1": "Félicitations ! Votre accès à {product_name}",
    "confirm_3_2": "C'est bon ! Votre produit {product_name} est confirmé.",
    "confirm_3_3": "Vous avez fait le bon choix. Bienvenue !",
    "confirm_3_4": "Juste à temps ! Vos accès à {product_name} sont ici.",
    "confirm_3_5": "Ouf ! Bienvenue. Votre produit {product_name} est confirmé.",
}

# ==============================================================================
# 3. TEMPLATES EMAIL HTML (TEXTES OPTIMISÉS ET NON-GÉNÉRIQUES)
# ==============================================================================
EMAIL_TEMPLATES = {
    "relance_t30": """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Un souci avec votre commande ?</title></head>
<body style="font-family:'Segoe UI',Arial,sans-serif;line-height:1.6;color:#333333;background-color:#f5f5f5;margin:0;padding:20px;">
  <div style="background-color:#ffffff;max-width:650px;margin:0 auto;box-shadow:0 5px 15px rgba(0,0,0,0.08);border-radius:8px;overflow:hidden;">
    <div style="color:#ffffff;text-align:center;padding:30px 20px;background:linear-gradient(135deg, #0078D4 0%, #004E8C 100%);">
      <h1 style="margin:0;font-size:24px;line-height:1.3;">Un souci avec votre commande ?</h1>
    </div>
    <div style="padding:30px 25px;">
      <p>Salut {customer_first_name},</p>
      <p>Je suis Yanick, le fondateur de Digitech Hub.</p>
      <p>J'ai vu que vous aviez commencé à commander le produit <strong>{product_name}</strong> il y a quelques minutes, mais que vous n'avez pas pu finaliser.</p>
      <p>Souvent, c'est juste un petit souci technique. Pour vous éviter de tout recommencer, j'ai mis votre commande de côté.</p>
      <table border="0" cellpadding="0" cellspacing="0" width="100%"><tr><td align="center" style="padding: 20px 0;">
        <a href="{checkout_url}" target="_blank" style="background-color:#0078D4;color:#ffffff;padding:12px 25px;border-radius:5px;text-decoration:none;font-weight:bold;display:inline-block;">Finaliser ma commande</a>
      </td></tr></table>
      <p>Si vous avez la moindre question, répondez simplement à cet email. Je suis là pour vous aider. 😉</p>
      <p>À tout de suite,<br><strong>Yanick Kouame</strong><br>Fondateur, Digitech Hub</p>
    </div>
    <div style="background-color:#F3F2F1;padding:20px;text-align:center;font-size:12px;color:#666666;">
      <p style="margin:0;">{store_name} © {current_year} | <a href="{store_url}" style="color:#0078D4;text-decoration:none;">Notre boutique</a></p>
    </div>
  </div>
</body></html>""",

    "relance_t6h": """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Concernant votre commande</title></head>
<body style="font-family:'Segoe UI',Arial,sans-serif;line-height:1.6;color:#333333;background-color:#f5f5f5;margin:0;padding:20px;">
  <div style="background-color:#ffffff;max-width:650px;margin:0 auto;box-shadow:0 5px 15px rgba(0,0,0,0.08);border-radius:8px;overflow:hidden;">
    <div style="color:#ffffff;text-align:center;padding:30px 20px;background:linear-gradient(135deg, #0078D4 0%, #004E8C 100%);">
      <h1 style="margin:0;font-size:24px;line-height:1.3;">Une question sur votre commande ?</h1>
    </div>
    <div style="padding:30px 25px;">
      <p>Salut {customer_first_name},</p>
      <p>Je reviens vers vous concernant le produit <strong>{product_name}</strong> que vous avez mis de côté.</p>
      <p>Chez Digitech Hub, notre mission est de vous donner accès à des solutions qui cassent les barrières numériques. Des centaines de clients nous font déjà confiance pour la qualité de nos produits et pour l'accompagnement humain derrière.</p>
      <p>D'ailleurs, ma garantie personnelle reste la même : si vous avez besoin d'aide, c’est <strong>moi, Yanick, qui vous assiste personnellement</strong>.</p>
      <table border="0" cellpadding="0" cellspacing="0" width="100%"><tr><td align="center" style="padding: 20px 0;">
        <a href="{checkout_url}" target="_blank" style="background-color:#0078D4;color:#ffffff;padding:12px 25px;border-radius:5px;text-decoration:none;font-weight:bold;display:inline-block;">Retrouver ma commande</a>
      </td></tr></table>
      <p>Passez une excellente journée,<br><strong>Yanick Kouame</strong><br>Fondateur, Digitech Hub</p>
    </div>
    <div style="background-color:#F3F2F1;padding:20px;text-align:center;font-size:12px;color:#666666;">
      <p style="margin:0;">{store_name} © {current_year} | <a href="{store_url}" style="color:#0078D4;text-decoration:none;">Notre boutique</a></p>
    </div>
  </div>
</body></html>""",

    "relance_t23h": """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Dernier rappel</title></head>
<body style="font-family:'Segoe UI',Arial,sans-serif;line-height:1.6;color:#333333;background-color:#f5f5f5;margin:0;padding:20px;">
  <div style="background-color:#ffffff;max-width:650px;margin:0 auto;box-shadow:0 5px 15px rgba(0,0,0,0.08);border-radius:8px;overflow:hidden;">
    <div style="color:#ffffff;text-align:center;padding:30px 20px;background:linear-gradient(135deg, #FF8C00 0%, #F7630C 100%);">
      <h1 style="margin:0;font-size:24px;line-height:1.3;">Dernières heures pour votre offre !</h1>
    </div>
    <div style="padding:30px 25px;">
      <p>Salut {customer_first_name},</p>
      <p>Ceci est un rappel amical avant l'expiration de votre offre.</p>
      <p>Dans quelques heures, l'avantage spécial lié à votre commande pour le produit <strong>{product_name}</strong> prendra fin.</p>
      <p>C'est le moment ou jamais d'investir dans les outils qui vous feront gagner du temps et en sérénité.</p>
      <table border="0" cellpadding="0" cellspacing="0" width="100%"><tr><td align="center" style="padding: 20px 0;">
        <a href="{checkout_url}" target="_blank" style="background-color:#F7630C;color:#ffffff;padding:12px 25px;border-radius:5px;text-decoration:none;font-weight:bold;display:inline-block;">Profiter de l'offre (dernier rappel)</a>
      </td></tr></table>
      <p><strong>Yanick Kouame</strong><br>Fondateur, Digitech Hub</p>
    </div>
    <div style="background-color:#F3F2F1;padding:20px;text-align:center;font-size:12px;color:#666666;">
      <p style="margin:0;">{store_name} © {current_year} | <a href="{store_url}" style="color:#0078D4;text-decoration:none;">Notre boutique</a></p>
    </div>
  </div>
</body></html>""",

    "relance_t47h": """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Clôture définitive</title></head>
<body style="font-family:'Segoe UI',Arial,sans-serif;line-height:1.6;color:#333333;background-color:#f5f5f5;margin:0;padding:20px;">
  <div style="background-color:#ffffff;max-width:650px;margin:0 auto;box-shadow:0 5px 15px rgba(0,0,0,0.08);border-radius:8px;overflow:hidden;">
    <div style="color:#ffffff;text-align:center;padding:30px 20px;background:linear-gradient(135deg, #D83B01 0%, #A80000 100%);">
      <h1 style="margin:0;font-size:24px;line-height:1.3;">Clôture définitive ce soir</h1>
    </div>
    <div style="padding:30px 25px;">
      <p>Salut {customer_first_name},</p>
      <p>Je vous contacte une toute dernière fois.</p>
      <p>Suite à la demande, j'ai exceptionnellement prolongé les conditions spéciales de 24 heures. Cette prolongation se termine <strong>définitivement ce soir, à minuit.</strong></p>
      <p>Passé ce délai, il ne sera plus possible d'obtenir <strong>{product_name}</strong> avec ses avantages actuels.</p>
      <table border="0" cellpadding="0" cellspacing="0" width="100%"><tr><td align="center" style="padding: 20px 0;">
        <a href="{checkout_url}" target="_blank" style="background-color:#A80000;color:#ffffff;padding:12px 25px;border-radius:5px;text-decoration:none;font-weight:bold;display:inline-block;">Saisir ma dernière chance</a>
      </td></tr></table>
      <p><strong>Yanick Kouame</strong><br>Fondateur, Digitech Hub</p>
    </div>
    <div style="background-color:#F3F2F1;padding:20px;text-align:center;font-size:12px;color:#666666;">
      <p style="margin:0;">{store_name} © {current_year} | <a href="{store_url}" style="color:#0078D4;text-decoration:none;">Notre boutique</a></p>
    </div>
  </div>
</body></html>""",

    # --- CONFIRMATIONS AVEC VOTRE BLOC STATIQUE INTÉGRÉ ---
    "confirm_3_1": """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Confirmation de commande</title></head>
<body style="font-family:'Segoe UI',Arial,sans-serif;line-height:1.6;color:#333333;background-color:#f5f5f5;margin:0;padding:20px;">
  <div style="background-color:#ffffff;max-width:650px;margin:0 auto;box-shadow:0 5px 15px rgba(0,0,0,0.08);border-radius:8px;overflow:hidden;">
    <div style="color:#ffffff;text-align:center;padding:30px 20px;background:linear-gradient(135deg, #107C10 0%, #0B530B 100%);">
      <h1 style="margin:0;font-size:24px;line-height:1.3;">Félicitations et bienvenue !</h1>
    </div>
    <div style="padding:30px 25px;">
      <p>Salut {customer_first_name},</p>
      <p>Bienvenue dans la famille Digitech Hub ! Vous avez fait un excellent choix pour investir dans votre réussite.</p>
      """ + Message_commun_html + """
    </div>
    <div style="background-color:#F3F2F1;padding:20px;text-align:center;font-size:12px;color:#666666;">
      <p style="margin:0;">{store_name} © {current_year} | <a href="{store_url}" style="color:#0078D4;text-decoration:none;">Notre boutique</a></p>
    </div>
  </div>
</body></html>""",

    "confirm_3_2": """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Confirmation de commande</title></head>
<body style="font-family:'Segoe UI',Arial,sans-serif;line-height:1.6;color:#333333;background-color:#f5f5f5;margin:0;padding:20px;">
  <div style="background-color:#ffffff;max-width:650px;margin:0 auto;box-shadow:0 5px 15px rgba(0,0,0,0.08);border-radius:8px;overflow:hidden;">
    <div style="color:#ffffff;text-align:center;padding:30px 20px;background:linear-gradient(135deg, #107C10 0%, #0B530B 100%);">
      <h1 style="margin:0;font-size:24px;line-height:1.3;">Commande finalisée !</h1>
    </div>
    <div style="padding:30px 25px;">
      <p>Salut {customer_first_name},</p>
      <p>Super ! Je suis content de voir que vous avez pu finaliser votre commande. L'important est que vous y soyez arrivé.</p>
      """ + Message_commun_html + """
    </div>
    <div style="background-color:#F3F2F1;padding:20px;text-align:center;font-size:12px;color:#666666;">
      <p style="margin:0;">{store_name} © {current_year} | <a href="{store_url}" style="color:#0078D4;text-decoration:none;">Notre boutique</a></p>
    </div>
  </div>
</body></html>""",

    "confirm_3_3": """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Confirmation de commande</title></head>
<body style="font-family:'Segoe UI',Arial,sans-serif;line-height:1.6;color:#333333;background-color:#f5f5f5;margin:0;padding:20px;">
  <div style="background-color:#ffffff;max-width:650px;margin:0 auto;box-shadow:0 5px 15px rgba(0,0,0,0.08);border-radius:8px;overflow:hidden;">
    <div style="color:#ffffff;text-align:center;padding:30px 20px;background:linear-gradient(135deg, #107C10 0%, #0B530B 100%);">
      <h1 style="margin:0;font-size:24px;line-height:1.3;">Excellente décision !</h1>
    </div>
    <div style="padding:30px 25px;">
      <p>Salut {customer_first_name},</p>
      <p>Je suis convaincu que vous avez fait le bon choix pour avancer sur vos projets. Bienvenue !</p>
      """ + Message_commun_html + """
    </div>
    <div style="background-color:#F3F2F1;padding:20px;text-align:center;font-size:12px;color:#666666;">
      <p style="margin:0;">{store_name} © {current_year} | <a href="{store_url}" style="color:#0078D4;text-decoration:none;">Notre boutique</a></p>
    </div>
  </div>
</body></html>""",

    "confirm_3_4": """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Confirmation de commande</title></head>
<body style="font-family:'Segoe UI',Arial,sans-serif;line-height:1.6;color:#333333;background-color:#f5f5f5;margin:0;padding:20px;">
  <div style="background-color:#ffffff;max-width:650px;margin:0 auto;box-shadow:0 5px 15px rgba(0,0,0,0.08);border-radius:8px;overflow:hidden;">
    <div style="color:#ffffff;text-align:center;padding:30px 20px;background:linear-gradient(135deg, #107C10 0%, #0B530B 100%);">
      <h1 style="margin:0;font-size:24px;line-height:1.3;">Juste à temps !</h1>
    </div>
    <div style="padding:30px 25px;">
      <p>Salut {customer_first_name},</p>
      <p>Vous l'avez fait juste à temps, félicitations ! L'offre spéciale est maintenant terminée, mais votre accès est bien sécurisé.</p>
      """ + Message_commun_html + """
    </div>
    <div style="background-color:#F3F2F1;padding:20px;text-align:center;font-size:12px;color:#666666;">
      <p style="margin:0;">{store_name} © {current_year} | <a href="{store_url}" style="color:#0078D4;text-decoration:none;">Notre boutique</a></p>
    </div>
  </div>
</body></html>""",

    "confirm_3_5": """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Confirmation de commande</title></head>
<body style="font-family:'Segoe UI',Arial,sans-serif;line-height:1.6;color:#333333;background-color:#f5f5f5;margin:0;padding:20px;">
  <div style="background-color:#ffffff;max-width:650px;margin:0 auto;box-shadow:0 5px 15px rgba(0,0,0,0.08);border-radius:8px;overflow:hidden;">
    <div style="color:#ffffff;text-align:center;padding:30px 20px;background:linear-gradient(135deg, #107C10 0%, #0B530B 100%);">
      <h1 style="margin:0;font-size:24px;line-height:1.3;">Bienvenue !</h1>
    </div>
    <div style="padding:30px 25px;">
      <p>Salut {customer_first_name},</p>
      <p>Je suis vraiment content que vous ayez pu saisir cette toute dernière opportunité. Votre accès est maintenant sécurisé.</p>
      """ + Message_commun_html + """
    </div>
    <div style="background-color:#F3F2F1;padding:20px;text-align:center;font-size:12px;color:#666666;">
      <p style="margin:0;">{store_name} © {current_year} | <a href="{store_url}" style="color:#0078D4;text-decoration:none;">Notre boutique</a></p>
    </div>
  </div>
</body></html>""",
}

# ==============================================================================
# 4. TEMPLATES WHATSAPP (TEXTES OPTIMISÉS ET NON-GÉNÉRIQUES)
# ==============================================================================
TEMPLATES_WHATSAPP = {
    "relance_t30": (
        "Bonjour {customer_first_name}, c'est Yanick de Digitech Hub 👋\n"
        "J'ai vu que vous avez eu un souci en commandant votre produit *{product_name}*.\n"
        "Pas de panique, ça arrive ! J'ai gardé votre panier ici pour vous :\n"
        "👉 {checkout_url}\n"
        "Dites-moi si je peux vous aider ! 😉"
    ),

    "relance_t6h": (
        "Bonjour {customer_first_name} 😊\n"
        "Je reviens vers vous concernant votre commande.\n"
        "Je sais qu'on peut parfois hésiter, mais sachez que ma priorité est votre succès.\n"
        "Ma garantie perso : si vous avez besoin d'aide, c'est moi qui vous réponds directement.\n"
        "Votre produit *{product_name}* est toujours disponible ici :\n"
        "👉 {checkout_url}"
    ),

    "relance_t23h": (
        "{customer_first_name}, attention ! ⚠️\n"
        "Dernier rappel : l'offre spéciale sur votre produit *{product_name}* se termine ce soir.\n"
        "Ne passez pas à côté de ses conditions avantageuses !\n"
        "C'est votre dernière chance.\n"
        "👉 {checkout_url}"
    ),

    "relance_t47h": (
        "{customer_first_name}, dernier message.\n"
        "L'offre sur *{product_name}* se termine DÉFINITIVEMENT ce soir à minuit. 🕛\n"
        "Il n'y aura pas d'autre prolongation. C'est maintenant ou jamais.\n"
        "👉 {checkout_url}"
    ),

    # --- CONFIRMATIONS WHATSAPP (VOUVOIEMENT VÉRIFIÉ) ---
    "confirm_3_1": (
        "Félicitations {customer_first_name} ! 🎉 Votre produit *{product_name}* est activé. Vous avez fait un excellent choix.\n\n" + Message_commun_wa
    ),
    "confirm_3_2": (
        "Super {customer_first_name}, content que ça ait fonctionné ! 👍\nVotre produit *{product_name}* est maintenant sécurisé.\n\n" + Message_commun_wa
    ),
    "confirm_3_3": (
        "Excellente décision {customer_first_name} ! Vous ne le regretterez pas. 😉\n\n" + Message_commun_wa
    ),
    "confirm_3_4": (
        "Félicitations {customer_first_name}, vous l'avez eu juste à temps ! 🤝\nL'offre est terminée, mais votre produit *{product_name}* est bien au chaud.\n\n" + Message_commun_wa
    ),
    "confirm_3_5": (
        "YES {customer_first_name} ! Vraiment content que vous ayez pu en profiter avant la fermeture. Bienvenue ! 🙌\n\n" + Message_commun_wa
    ),
}


def render_email_with_brand(fragment_html: str, vars_dict: dict) -> str:
    """
    Génère un email HTML complet en utilisant le CSS "inliné" pour une compatibilité maximale.
    """
    header = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{vars_dict.get('email_subject', 'Notification de Digitech Hub')}</title>
</head>
<body style="font-family:'Segoe UI',Arial,sans-serif;line-height:1.6;color:#333333;background-color:#f5f5f5;margin:0;padding:20px;">
  
  <div style="max-width:650px;margin:0 auto;background-color:#ffffff;border-radius:8px;box-shadow:0 5px 15px rgba(0,0,0,0.08);overflow:hidden;">
    
    <div style="background:linear-gradient(135deg, {vars_dict.get('header_color_start', '#0D47A1')} 0%, {vars_dict.get('header_color_end', '#003b8b')} 100%);color:#ffffff;padding:30px 20px;text-align:center;">
      <h1 style="margin:0;font-size:24px;line-height:1.3;">{vars_dict.get('email_title', 'Un message pour vous')}</h1>
    </div>
    
    <div style="padding:30px 25px;">
"""
    
    footer = f"""
    </div>
    
    <div style="background-color:#F3F2F1;color:#666666;padding:20px;font-size:12px;text-align:center;">
      <p style="margin:0;">{vars_dict.get('store_name','Digitech Hub')} © {vars_dict.get('current_year','')} · 
        <a href="{vars_dict.get('store_url','#')}" style="color:#0D47A1;text-decoration:none;">Boutique</a>
      </p>
    </div>

  </div>
</body>
</html>
"""
    # On assemble le tout et on remplace les placeholders du fragment
    full_html_content = header + fragment_html + footer
    return full_html_content.format(**vars_dict)