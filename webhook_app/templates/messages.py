# webhook_app/templates/messages.py

# -------------------------------------------
# Blocs communs réutilisables
# -------------------------------------------

Message_commun = """<div style="background:#F3F9FB;border:1px dashed #0078D4;padding:16px;border-radius:6px;margin:20px 0;">
  <p>Pour accéder dès maintenant à ta licence et aux bonus, voici la marche à suivre :</p>

  <p>Clique sur ce lien :
    <a href="https://portal.chariow.com" style="color:#0078D4;text-decoration:underline;">https://portal.chariow.com</a>
  </p>

  <p>Entre l'email que tu as utilisé pour ton achat : <strong>{customer_email}</strong></p>

  <p>Copie le code que tu recevras par email et colle-le sur le site.<br>
  Tu auras alors accès instantanément à tes achats.</p>

  <p>Et n'oublie pas : si tu rencontres la moindre difficulté pour l'installation, je suis là. Réponds simplement à ce message ou contacte-moi sur WhatsApp
    <a href="https://wa.me/2250576654850" style="color:#0078D4;text-decoration:underline;">wa.me/2250576654850</a>.
    C'est moi, Yanick, qui t'assisterai personnellement.
  </p>

  <p>Bienvenue dans l'aventure pour l'émancipation numérique à vie !<br>
  À très vite,<br>
  Yanick Kouame<br>
  Fondateur, Digitech Hub</p>
</div>"""


Message_commun_wa = (
    "📦 Comment accéder à votre licence :\n"
    "1. Allez sur : portal.chariow.com\n"
    "2. Entrez votre email : {customer_email}\n"
    "3. Validez avec le code de vérification (envoyé sur votre mail)\n\n"
    "⏱ Le code arrive généralement immédiatement - Vérifiez vos spams si besoin.\n"
)

# -------------------------------------------
# Sujets d'e-mail
# -------------------------------------------

EMAIL_SUBJECTS = {
    # Événements directs
    "success": "Confirmation de commande -",
    "failure": "Paiement échoué -",
    "abandon": "Votre panier vous attend -",

    # Relances
    "relance_t30":  "Un souci avec ta commande Microsoft 365 ?",
    "relance_t6h":  "{customer_first_name}, est-ce que {price_current_fmt} pour Microsoft 365 à vie, c'est trop beau pour être vrai ?",
    "relance_t23h": "Plus que quelques heures : ton accès à vie à Microsoft 365 pour {price_current_fmt}",
    "relance_t47h": "Fermeture définitive de l'offre Microsoft 365 à {price_current_fmt} ce soir",

    # Confirmations 3.1 → 3.5
    "confirm_3_1": "Félicitations ! Voici tes accès à Microsoft 365 à vie.",
    "confirm_3_2": "C'est bon ! Ta licence Microsoft 365 est activée.",
    "confirm_3_3": "Tu as fait le bon choix. Bienvenue chez Digitech Hub !",
    "confirm_3_4": "Juste à temps ! Tes accès Microsoft 365 sont ici.",
    "confirm_3_5": "Ouf ! Bienvenue. Ta licence Microsoft 365 est confirmée.",
}

# -------------------------------------------
# Templates e-mail (HTML)
# -------------------------------------------

EMAIL_TEMPLATES = {

    # Relances e-mail (plain text pour simplicité — tu peux garder HTML si tu préfères)
    "relance_t30": """<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:'Segoe UI',Arial,sans-serif;line-height:1.6;color:#2d2d2d;background:#f5f5f5;margin:0;">
  <div style="background:#fff;max-width:650px;margin:0 auto;box-shadow:0 3px 10px rgba(0,0,0,0.1);border-radius:8px;">
    <div style="color:#fff;text-align:center;padding:30px 20px;border-radius:8px 8px 0 0;background:linear-gradient(135deg,#0078D4 0%,#004E8C 100%);">
      <h1 style="margin:0;font-size:22px;">Un souci avec ta commande Microsoft 365&nbsp;?</h1>
    </div>
    <div style="padding:25px;">
      <p>Salut {customer_first_name},</p>

      <p>Je suis Yanick, le fondateur de Digitech Hub.</p>

      <p>J'ai vu que tu avais commencé à commander ta licence Microsoft 365 à vie il y a quelques minutes, mais que tu n'as pas pu aller jusqu'au bout.</p>

      <p>Je voulais juste m'assurer que tout allait bien de ton côté.</p>

      <p>Souvent, c'est juste un petit caprice du réseau ou un souci avec le paiement Mobile Money qui bloque au dernier moment. Ça arrive, pas de panique&nbsp;!</p>

      <p>Pour t'éviter de tout recommencer, j'ai mis ta commande de côté. Tu peux la retrouver et finaliser ton achat en cliquant sur le lien ci-dessous&nbsp;:</p>

      <p style="text-align:center;margin:16px 0 24px;">
        👉 <a href="{checkout_url}" style="color:#0078D4;text-decoration:underline;word-break:break-all;">Clique ici pour finaliser ma commande et activer ma licence</a>
      </p>

      <p>Si tu as la moindre question ou si ça coince quelque part, réponds simplement à cet email. C'est moi qui lis et qui réponds, et je suis là pour t'aider. L'idée est de te libérer des contraintes, pas d'en ajouter. 😉</p>

      <p>À tout de suite,</p>

      <p style="margin:0;">Yanick Kouame<br>Fondateur, Digitech Hub</p>
    </div>
    <div style="background:#F3F2F1;padding:20px;text-align:center;font-size:12px;color:#666;border-radius:0 0 8px 8px;">
      <p style="margin:0;">{store_name} © {current_year} | <a href="{store_url}" style="color:#0078D4;">Notre boutique</a></p>
    </div>
  </div>
</body></html>""",

    "relance_t6h": """<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:'Segoe UI',Arial,sans-serif;line-height:1.6;color:#2d2d2d;background:#f5f5f5;margin:0;">
  <div style="background:#fff;max-width:650px;margin:0 auto;box-shadow:0 3px 10px rgba(0,0,0,0.1);border-radius:8px;">
    <div style="color:#fff;text-align:center;padding:30px 20px;border-radius:8px 8px 0 0;background:linear-gradient(135deg,#0078D4 0%,#004E8C 100%);">
      <h1 style="margin:0;font-size:22px;">Des questions sur l’offre {price_current_fmt}&nbsp;?</h1>
    </div>
    <div style="padding:25px;">
      <p>Salut {customer_first_name},</p>

      <p>Je reviens vers toi concernant <strong>{product_name}</strong> que tu as mise de côté tout à l'heure.</p>

      <p>Honnêtement, je comprends l'hésitation. Une offre aussi directe, ça peut soulever des questions. <strong>"Un accès à vie pour le prix d'un mois d'abonnement, est-ce que c'est sérieux&nbsp;?"</strong></p>

      <p>Laisse-moi te dire pourquoi j'ai créé Digitech Hub. En Afrique, j'ai vu trop de projets brillants ralentis ou bloqués par des barrières numériques. Un abonnement qui expire au mauvais moment, le danger d'un logiciel piraté... Chaque obstacle est une opportunité perdue.</p>

      <p>Ma mission, c'est de casser ces chaînes. De te donner accès à l'outil numéro 1 dans le monde, sans t'étrangler avec des paiements récurrents. C'est ça, l’émancipation numérique à vie, sans abonnement.</p>

      <p>Et cette mission, plus de 320 entrepreneurs, PME et secrétaires de Côte d’ivoire, du Sénégal ou encore du Cameroun l'ont déjà rejointe.</p>

      <p>Mais la meilleure garantie que je puisse t'offrir, c'est celle-ci&nbsp;:</p>

      <p>Quand tu achètes chez Digitech Hub, c’est moi, Yanick Kouame, qui t’assiste personnellement sur WhatsApp ou en appel vidéo Google Meet. Pas un robot, pas un centre d’appel lointain. Un humain qui comprend ton quotidien et qui reste disponible jusqu’à ce que ta licence soit activée sur tes 5 appareils.</p>

      <p>Ton panier est toujours réservé. Il contient&nbsp;:</p>

      <p style="margin:12px 0 0 0;">✅ Un accès à vie à toute la suite Microsoft 365 (Word, Excel, etc.)<br>
      ✅ Une licence 100% légale pour 5 PC Windows ou MacBook<br>
      ✅ Mon assistance personnelle jusqu'à l'activation complète<br>
      ✅ En bonus : La formation vidéo complète pour maîtriser Office</p>

      <p>Ne laisse pas un doute te priver de cette tranquillité.</p>

      <p style="text-align:center;margin:16px 0 24px;">
        👉 <a href="{checkout_url}" style="color:#0078D4;text-decoration:underline;word-break:break-all;">Oui, je saisis ma licence à vie avec l'assistance de Yanick</a>
      </p>

      <p>Passe une excellente journée,</p>

      <p style="margin:0;">Yanick Kouame<br>Fondateur, Digitech Hub</p>
    </div>
    <div style="background:#F3F2F1;padding:20px;text-align:center;font-size:12px;color:#666;border-radius:0 0 8px 8px;">
      <p style="margin:0;">{store_name} © {current_year} | <a href="{store_url}" style="color:#0078D4;">Notre boutique</a></p>
    </div>
  </div>
</body></html>""",

    "relance_t23h": """<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:'Segoe UI',Arial,sans-serif;line-height:1.6;color:#2d2d2d;background:#f5f5f5;margin:0;">
  <div style="background:#fff;max-width:650px;margin:0 auto;box-shadow:0 3px 10px rgba(0,0,0,0.1);border-radius:8px;">
    <div style="color:#fff;text-align:center;padding:30px 20px;border-radius:8px 8px 0 0;background:linear-gradient(135deg,#FF8C00 0%,#F7630C 100%);">
      <h1 style="margin:0;font-size:22px;">Dernières heures pour l’offre à {price_current_fmt}</h1>
    </div>
    <div style="padding:25px;">
      <p>Salut {customer_first_name},</p>

      <p>Ceci est le dernier rappel avant la fin de l'offre.</p>

      <p>Dans quelques heures, l'opportunité d'obtenir la licence Microsoft 365 + Windows 11 Pro à vie pour {price_current_fmt} disparaît. Le tarif repassera définitivement à {price_after_fmt}.</p>

      <p>Très simplement, voici le choix qui se présente à toi aujourd'hui&nbsp;:</p>

      <p><strong>Option 1</strong> : Laisser passer l'offre.<br>
      Tu continues comme avant, avec le stress des rappels d'abonnement, les documents qui se bloquent au pire moment et les risques des logiciels non officiels.</p>

      <p><strong>Option 2</strong> : Saisir l'opportunité.<br>
      Tu investis une seule fois {price_current_fmt} et tu obtiens la tranquillité numérique à vie. Word, Excel, PowerPoint, etc., toujours à jour et 100% fonctionnels sur tes 10 appareils.</p>

      <p>Ce que tu es sur le point de manquer n'est pas juste une promotion, c'est&nbsp;:</p>

      <p>Une économie immédiate de {price_after_fmt} - {price_current_fmt}.<br>
      La fin DÉFINITIVE des frais d'abonnement.<br>
      Mon assistance personnelle et directe pour tout installer sans souci.</p>

      <p>La balle est dans ton camp. C'est le moment ou jamais de t'émanciper de la contrainte des abonnements.</p>

      <p>Ton panier initial a été conservé et l'offre y est toujours appliquée, mais plus pour longtemps.</p>

      <p style="text-align:center;margin:16px 0 24px;">
        👉 <a href="{checkout_url}" style="color:#0078D4;text-decoration:underline;word-break:break-all;">Je sécurise mon accès à vie avant la fin (dernier rappel)a>
      </p>

      <p>Yanick Kouame<br>Fondateur, Digitech Hub</p>
    </div>
    <div style="background:#F3F2F1;padding:20px;text-align:center;font-size:12px;color:#666;border-radius:0 0 8px 8px;">
      <p style="margin:0;">{store_name} © {current_year} | <a href="{store_url}" style="color:#0078D4;">Notre boutique</a></p>
    </div>
  </div>
</body></html>""",

    "relance_t47h": """<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:'Segoe UI',Arial,sans-serif;line-height:1.6;color:#2d2d2d;background:#f5f5f5;margin:0;">
  <div style="background:#fff;max-width:650px;margin:0 auto;box-shadow:0 3px 10px rgba(0,0,0,0.1);border-radius:8px;">
    <div style="color:#fff;text-align:center;padding:30px 20px;border-radius:8px 8px 0 0;background:linear-gradient(135deg,#D83B01 0%,#A80000 100%);">
      <h1 style="margin:0;font-size:22px;">Clôture définitive ce soir</h1>
    </div>
    <div style="padding:25px;">
      <p>Salut {customer_first_name},</p>

      <p>Je te contacte une toute dernière fois au sujet de la licence Microsoft 365 à vie.</p>

      <p>Hier soir, au moment où l'offre devait expirer, j'ai reçu plusieurs messages de personnes ayant eu des soucis de paiement. Pour que tout le monde ait une chance équitable, j'ai exceptionnellement prolongé l'offre de 24 heures.</p>

      <p>Cette prolongation se termine ce soir, à minuit.</p>

      <p>Passé ce délai, la page de l'offre au tarif de <strong>{price_current_fmt}</strong> sera définitivement désactivée.</p>

      <p>Pour résumer de la façon la plus simple possible, voici ce que tu obtiens si tu agis maintenant&nbsp;:</p>

      <p>Un produit essentiel : La suite Microsoft complète (Word, Excel, etc.) + Windows 11 Pro.<br>
      Un prix imbattable : <strong>{price_current_fmt}</strong>, payé une seule fois.<br>
      Une valeur à vie : Fini les abonnements, pour toujours.<br>
      Une garantie humaine : Mon aide personnelle et directe pour que tout fonctionne.</p>

      <p>Il n'y aura pas d'autre prolongation ni d'autre rappel.</p>

      <p>Si l'idée de t'émanciper des abonnements pour de bon te parle, c'est maintenant.</p>

      <p style="text-align:center;margin:16px 0 24px;">
        👉 <a href="{checkout_url}" style="color:#0078D4;text-decoration:underline;word-break:break-all;">Je réclame mon accès à vie avant la fermeture définitive</a>
      </p>

      <p>Yanick Kouame<br>Fondateur, Digitech Hub</p>
    </div>
    <div style="background:#F3F2F1;padding:20px;text-align:center;font-size:12px;color:#666;border-radius:0 0 8px 8px;">
      <p style="margin:0;">{store_name} © {current_year} | <a href="{store_url}" style="color:#0078D4;">Notre boutique</a></p>
    </div>
  </div>
</body></html>""",

    # Confirmations selon parcours (3.1 → 3.5)
    "confirm_3_1": """Salut {customer_first_name},
Félicitations et bienvenue dans la famille Digitech Hub !
Tu as fait le meilleur choix pour ta tranquillité et ta productivité. Fini le stress des abonnements mensuels, à toi la liberté numérique à vie !

{Message_commun}""",

    "confirm_3_2": """Salut {customer_first_name},
Super ! Je suis content de voir que tu as pu finaliser ta commande. Parfois, la technique fait des siennes, mais l'important est que tu y sois arrivé.

{Message_commun}""",

    "confirm_3_3": """Salut {customer_first_name},
Excellente décision ! Tu rejoins les 320+ entrepreneurs qui ont dit adieu aux abonnements.

{Message_commun}""",

    "confirm_3_4": """Salut {customer_first_name},
Tu l'as fait juste à temps, félicitations ! L'offre spéciale est maintenant terminée, mais ta licence à vie est bien sécurisée.

{Message_commun}""",

    "confirm_3_5": """Salut {customer_first_name},
Vraiment content que tu aies pu saisir cette toute dernière opportunité avant la fermeture définitive. Bienvenue ! 🙌

{Message_commun}""",
}

# -------------------------------------------
# Templates WhatsApp
# -------------------------------------------

TEMPLATES_WHATSAPP = {
    
    # Relances WhatsApp
    "relance_t30": (
        "Salut {customer_first_name}, c'est Yanick de Digitech Hub 👋\n"
        "J'ai vu que tu as essayé de prendre ta licence Microsoft 365 mais que ça n'a pas abouti.\n"
        "Souvent, c'est juste un petit bug avec le paiement Mobile Money, t'inquiète pas ! Ça arrive.\n"
        "J'ai gardé ton panier de côté pour toi. Tu peux réessayer directement ici :\n"
        "👉 {checkout_url}\n"
        "Dis-moi si ça bloque quelque part, je suis là pour aider ! 😉"
    ),

    "relance_t6h": (
        "Salut {customer_first_name} 😊\n"
        "Je comprends que l'offre pour Microsoft 365 à vie à {price_current_fmt} puisse faire hésiter.\n"
        "Mon but chez Digitech Hub est simple : casser les barrières du numérique en Afrique. "
        "Rendre les meilleurs outils accessibles à tous, sans la contrainte des abonnements.\n"
        "C'est pour ça que +320 entrepreneurs nous font déjà confiance.\n"
        "Et ma garantie personnelle : si tu as besoin d'aide, c'est moi, Yanick, qui te réponds et qui t'assiste pour tout installer. Pas de robot.\n"
        "Ne laisse pas un doute te priver de cette tranquillité !\n"
        "👉 {checkout_url}"
    ),

    "relance_t23h": (
        "{customer_first_name}, attention ! ⚠️\n"
        "Dernier rappel : l'offre pour ta licence Microsoft 365 à vie à {price_current_fmt} se termine ce soir.\n"
        "Après, le prix repasse à {price_after_fmt}.\n"
        "Le choix est simple :\n"
        "❌ Continuer avec le stress des abonnements.\n"
        "✅ Payer 1 seule fois et avoir la paix pour TOUJOURS.\n"
        "C'est ta dernière chance de saisir cette économie. Ne la manque pas !\n"
        "👉 {checkout_url}"
    ),

    "relance_t47h": (
        "{customer_first_name}, dernier message.\n"
        "Suite à des bugs de paiement hier, j'ai exceptionnellement prolongé l'offre. "
        "Elle se termine DÉFINITIVEMENT ce soir à minuit. 🕛\n"
        "Après cette heure, la page de paiement sera désactivée.\n"
        "Si tu veux ta licence à vie à {price_current_fmt}, c'est maintenant ou jamais. Il n'y aura pas d'autre chance.\n"
        "👉 {checkout_url}"
    ),

    # Confirmations WhatsApp 3.1 → 3.5
    "confirm_3_1": (
        "Félicitations {customer_first_name} ! 🎉 Ta licence Microsoft 365 à vie est activée. "
        "Tu as fait un excellent choix.\n"
        "{Message_commun_wa}"
        "Si t'as besoin d'aide pour l'installation, je suis là ! Dis-moi juste. 😉\n\n"
        "-Yanick"
    ),
    "confirm_3_2": (
        "Super {customer_first_name}, content que ça ait marché ! 👍\n"
        "Ta licence à vie est maintenant sécurisée.\n"
        "{Message_commun_wa}"
        "Dis-moi si tu as besoin d'un coup de main pour l'installation !\n\n"
        "-Yanick"
    ),
    "confirm_3_3": (
        "Excellente décision {customer_first_name} ! Tu ne le regretteras pas. 😉\n"
        "Bienvenue dans le mouvement pour l'émancipation numérique.\n"
        "{Message_commun_wa}"
        "Je suis là si tu as des questions.\n\n"
        "-Yanick"
    ),
    "confirm_3_4": (
        "Félicitations {customer_first_name}, tu l'as eu juste à temps ! 🤝\n"
        "L'offre est terminée, mais ta licence à vie est bien au chaud.\n"
        "{Message_commun_wa}"
        "Bienvenue !\n\n"
        "-Yanick"
    ),
    "confirm_3_5": (
        "YES {customer_first_name} ! Vraiment content que tu aies pu en profiter avant la fermeture définitive. Bienvenue ! 🙌\n"
        "{Message_commun_wa}"
        "Profites-en bien !\n\n"
        "-Yanick"
    ),
}



def render_email_with_brand(fragment_html: str, vars_dict: dict) -> str:
    # Amélioration possible du css (inline CSS only pour la compatibilité)
    header = f"""
<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
  body{{font-family:'Segoe UI',Arial,sans-serif;line-height:1.6;color:#2d2d2d;background:#f5f5f5;margin:0}}
  .container{{max-width:650px;margin:0 auto;background:#fff;border-radius:8px;box-shadow:0 3px 10px rgba(0,0,0,.08);overflow:hidden}}
  .head{{background:linear-gradient(135deg,#0D47A1 0%,#003b8b 100%);color:#fff;padding:24px 20px}}
  .content{{padding:24px}}
  .btn{{background:#0D47A1;color:#fff;text-decoration:none;padding:12px 20px;border-radius:6px;display:inline-block}}
  .footer{{background:#F3F2F1;color:#666;padding:16px 20px;font-size:12px;text-align:center}}
  a{{color:#0D47A1}}
</style></head><body><div class="container">
<div class="head"><h1 style="margin:0;font-size:20px;">{vars_dict.get('store_name','')}</h1></div>
<div class="content">
"""
    footer = f"""
</div>
<div class="footer">
  {vars_dict.get('store_name','')} © {vars_dict.get('current_year','')} ·
  <a href="{vars_dict.get('store_url','#')}">Boutique</a>
</div>
</div></body></html>
"""
    return header + fragment_html + footer
