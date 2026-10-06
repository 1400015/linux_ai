"""Internationalization support with English fallback.

Texts are authored in English and translated via the CATALOG dict.
If a language (or a specific text) has no translation, the English
string is returned. The active language is read from the
``app.language`` config key and can be changed with :func:`set_language`.
"""

import locale
import os

TRANSLATIONS = {
    "pt": {
        "Ready": "Pronto",
        "Type your message... (Enter to send)": "Escreva a sua mensagem... (Enter para enviar)",
        "Enter your API Key": "Insira a sua API Key",
        "Note: the {var} environment variable overrides this key.":
            "Nota: a variável de ambiente {var} substitui esta chave.",
        "Reload saved keys": "Recarregar chaves guardadas",
        "Copy effective key to config": "Copiar chave efetiva para o config",
        "Key copied to config.json": "Chave copiada para o config.json",
        "User": "Utilizador",
        "AI": "IA",
        "System": "Sistema",
        "Thinking...": "A pensar...",
        "Welcome to Linux AI Assistant!\nType a message or press Ctrl+S to capture the screen.":
            "Bem-vindo ao Linux AI Assistant!\nEscreva uma mensagem ou pressione Ctrl+S para capturar o ecrã.",
        "Settings": "Configurações",
        "Conversation History": "Histórico de Conversas",
        "Statistics": "Estatísticas",
        "Quit": "Sair",
        "Name:": "Nome:",
        "Description:": "Descrição:",
        "Colors": "Cores",
        "Background:": "Fundo:",
        "Text:": "Texto:",
        "Accent:": "Destaque:",
        "Usage Statistics": "Estatísticas de Uso",
        "Reset Statistics": "Resetar Estatísticas",
        "API Configuration": "Configuração de API",
        "AI Provider:": "Provedor de IA:",
        "API Key:": "API Key:",
        "UI Configuration": "Configuração de UI",
        "Window opacity:": "Opacidade da janela:",
        "Always visible": "Sempre visível",
        "Theme:": "Tema:",
        "Language:": "Idioma:",
        "Manage Themes": "Gerir Temas",
        "Docked (reserves screen space)": "Ancorado (reserva espaço no ecrã)",
        "Dock edge:": "Borda do dock:",
        "Appearance": "Aparência",
        "Custom Themes": "Temas Personalizados",
        "Add Theme": "Adicionar Tema",
        "Remove Theme": "Remover Tema",
        "Themes": "Temas",
        "Features": "Funcionalidades",
        "Screen capture": "Captura de ecrã",
        "OCR (text recognition)": "OCR (Reconhecimento de texto)",
        "Expert Mode": "Modo Especialista",
        "Show Expert Mode button": "Mostrar o botão de Modo Especialista",
        "Right": "Direita",
        "Left": "Esquerda",
        "Top": "Topo",
        "Bottom": "Fundo",
        "Settings saved successfully": "Configurações guardadas com sucesso",
        "Settings - Linux AI Assistant": "Configurações - Linux AI Assistant",
        "Statistics - Linux AI Assistant": "Estatísticas - Linux AI Assistant",
        "Conversation History - Linux AI Assistant": "Histórico de Conversas - Linux AI Assistant",
        "New file": "Novo ficheiro",
        "Proposed changes": "Alterações propostas",
        "Cancel": "Cancelar",
        "Write file": "Escrever ficheiro",
        "Show Window": "Mostrar Janela",
        "Linux AI Assistant": "Linux AI Assistant",
        "Menu": "Menu",
        "Close": "Fechar",
        "Minimize": "Minimizar",
        "Processing...": "A processar...",
        "Run": "Executar",
        "Done.": "Concluído.",
        "Failed.": "Falhou.",
        "Offline mode: answering from local knowledge.":
            "Modo offline: a responder a partir do conhecimento local.",
        "Run suggested commands?": "Executar os comandos sugeridos?",
        "These changes need administrator rights (pkexec):":
            "Estas alterações precisam de direitos de administrador (pkexec):",
        "Cancelled": "Cancelado",
        "Streaming cancelled": "Transmissão cancelada",
        "New response received": "Nova resposta recebida",
        "Send (Enter)": "Enviar (Enter)",
        "Capture screen (Ctrl+S)": "Capturar ecrã (Ctrl+S)",
        "Expert Mode (Ctrl+E)": "Modo Especialista (Ctrl+E)",
        "Screen capture is disabled in settings.": "A captura de ecrã está desativada nas definições.",
        "Wait for the current message to be processed": "Aguarde a mensagem atual ser processada",
        "Capturing screen...": "A capturar o ecrã...",
        "No theme selected": "Nenhum tema selecionado",
        "Cannot remove built-in themes": "Não é possível remover temas incorporados",
        "A theme name is required (letters, digits, '-' or '_', max 64)":
            "É necessário um nome de tema (letras, dígitos, '-' ou '_', máx. 64)",
        "Expert Mode ENABLED - Helping with system configuration":
            "Modo Especialista ATIVADO - A ajudar com a configuração do sistema",
        "Expert Mode enabled": "Modo Especialista ativado",
        "Expert Mode DISABLED": "Modo Especialista DESATIVADO",
        "Expert Mode disabled": "Modo Especialista desativado",
        "Hide Window": "Esconder Janela",
        "Path not allowed: {path}": "Caminho não permitido: {path}",
        "Error: {error}": "Erro: {error}",
        "Screen captured: {path}": "Ecrã capturado: {path}",
        "Extracting text from the image...": "A extrair texto da imagem...",
        "Could not extract text from the image.": "Não foi possível extrair texto da imagem.",
        "OCR disabled in settings.": "OCR desativado nas definições.",
        "Error capturing screen: {detail}": "Erro ao capturar o ecrã: {detail}",
        "(text truncated)": "(texto truncado)",
        "File written: {path}": "Ficheiro escrito: {path}",
        "Error writing file: {detail}": "Erro ao escrever o ficheiro: {detail}",
        "WARNING: preview truncated at 1 MB; the file is bigger and this diff is NOT complete.":
            "AVISO: pré-visualização truncada a 1 MB; o ficheiro é maior e este diff NÃO está completo.",
        "File changed since the preview was shown; review it again: {path}":
            "O ficheiro mudou desde a pré-visualização; reveja novamente: {path}",
    },
    "es": {
        "Ready": "Listo",
        "Type your message... (Enter to send)": "Escribe tu mensaje... (Enter para enviar)",
        "Enter your API Key": "Introduce tu API Key",
        "Note: the {var} environment variable overrides this key.":
            "Nota: la variable de entorno {var} sustituye esta clave.",
        "Reload saved keys": "Recargar claves guardadas",
        "Copy effective key to config": "Copiar clave efectiva al config",
        "Key copied to config.json": "Clave copiada a config.json",
        "User": "Usuario",
        "AI": "IA",
        "System": "Sistema",
        "Thinking...": "Pensando...",
        "Welcome to Linux AI Assistant!\nType a message or press Ctrl+S to capture the screen.":
            "Bienvenido a Linux AI Assistant!\nEscribe un mensaje o pulsa Ctrl+S para capturar la pantalla.",
        "Settings": "Ajustes",
        "Conversation History": "Historial de conversaciones",
        "Statistics": "Estadísticas",
        "Quit": "Salir",
        "Name:": "Nombre:",
        "Description:": "Descripción:",
        "Colors": "Colores",
        "Background:": "Fondo:",
        "Text:": "Texto:",
        "Accent:": "Acento:",
        "Usage Statistics": "Estadísticas de uso",
        "Reset Statistics": "Reiniciar estadísticas",
        "API Configuration": "Configuración de API",
        "AI Provider:": "Proveedor de IA:",
        "UI Configuration": "Configuración de UI",
        "Window opacity:": "Opacidad de la ventana:",
        "Always visible": "Siempre visible",
        "Theme:": "Tema:",
        "Language:": "Idioma:",
        "Manage Themes": "Gestionar temas",
        "Docked (reserves screen space)": "Acoplado (reserva espacio en pantalla)",
        "Dock edge:": "Borde del dock:",
        "Appearance": "Apariencia",
        "Custom Themes": "Temas personalizados",
        "Add Theme": "Añadir tema",
        "Remove Theme": "Eliminar tema",
        "Themes": "Temas",
        "Features": "Características",
        "Screen capture": "Captura de pantalla",
        "OCR (text recognition)": "OCR (reconocimiento de texto)",
        "Expert Mode": "Modo experto",
        "Show Expert Mode button": "Mostrar el botón de Modo experto",
        "Right": "Derecha",
        "Left": "Izquierda",
        "Top": "Arriba",
        "Bottom": "Abajo",
        "Settings saved successfully": "Ajustes guardados correctamente",
        "Settings - Linux AI Assistant": "Ajustes - Linux AI Assistant",
        "Statistics - Linux AI Assistant": "Estadísticas - Linux AI Assistant",
        "Conversation History - Linux AI Assistant": "Historial de conversaciones - Linux AI Assistant",
        "New file": "Nuevo archivo",
        "Proposed changes": "Cambios propuestos",
        "Cancel": "Cancelar",
        "Write file": "Escribir archivo",
        "Show Window": "Mostrar ventana",
        "Menu": "Menú",
        "Close": "Cerrar",
        "Minimize": "Minimizar",
        "Processing...": "Procesando...",
        "Run": "Ejecutar",
        "Done.": "Hecho.",
        "Failed.": "Falló.",
        "Offline mode: answering from local knowledge.":
            "Modo sin conexión: respondiendo con el conocimiento local.",
        "Run suggested commands?": "¿Ejecutar los comandos sugeridos?",
        "These changes need administrator rights (pkexec):":
            "Estos cambios requieren derechos de administrador (pkexec):",
        "Cancelled": "Cancelado",
        "Streaming cancelled": "Transmisión cancelada",
        "New response received": "Nueva respuesta recibida",
        "Send (Enter)": "Enviar (Enter)",
        "Capture screen (Ctrl+S)": "Capturar pantalla (Ctrl+S)",
        "Expert Mode (Ctrl+E)": "Modo experto (Ctrl+E)",
        "Screen capture is disabled in settings.": "La captura de pantalla está desactivada en los ajustes.",
        "Wait for the current message to be processed": "Espera a que se procese el mensaje actual",
        "Capturing screen...": "Capturando la pantalla...",
        "No theme selected": "Ningún tema seleccionado",
        "Cannot remove built-in themes": "No se pueden eliminar los temas integrados",
        "A theme name is required (letters, digits, '-' or '_', max 64)":
            "Se necesita un nombre de tema (letras, dígitos, '-' o '_', máx. 64)",
        "Expert Mode ENABLED - Helping with system configuration":
            "Modo experto ACTIVADO - Ayudando con la configuración del sistema",
        "Expert Mode enabled": "Modo experto activado",
        "Expert Mode DISABLED": "Modo experto DESACTIVADO",
        "Expert Mode disabled": "Modo experto desactivado",
        "Hide Window": "Ocultar ventana",
        "Path not allowed: {path}": "Ruta no permitida: {path}",
        "Error: {error}": "Error: {error}",
        "Screen captured: {path}": "Pantalla capturada: {path}",
        "Extracting text from the image...": "Extrayendo texto de la imagen...",
        "Could not extract text from the image.": "No se pudo extraer texto de la imagen.",
        "OCR disabled in settings.": "OCR desactivado en los ajustes.",
        "Error capturing screen: {detail}": "Error al capturar la pantalla: {detail}",
        "(text truncated)": "(texto truncado)",
        "File written: {path}": "Archivo escrito: {path}",
        "Error writing file: {detail}": "Error al escribir el archivo: {detail}",
        "WARNING: preview truncated at 1 MB; the file is bigger and this diff is NOT complete.":
            "ADVERTENCIA: vista previa truncada a 1 MB; el archivo es mayor y este diff NO está completo.",
        "File changed since the preview was shown; review it again: {path}":
            "El archivo cambió desde la vista previa; revísalo de nuevo: {path}",
        "API Key:": "Clave API:",
    },
    "fr": {
        "Ready": "Prêt",
        "Type your message... (Enter to send)": "Tapez votre message... (Entrée pour envoyer)",
        "Enter your API Key": "Saisissez votre clé API",
        "Note: the {var} environment variable overrides this key.":
            "Remarque : la variable d'environnement {var} remplace cette clé.",
        "Reload saved keys": "Recharger les clés enregistrées",
        "Copy effective key to config": "Copier la clé effective dans la config",
        "Key copied to config.json": "Clé copiée dans config.json",
        "User": "Utilisateur",
        "AI": "IA",
        "System": "Système",
        "Thinking...": "Réflexion...",
        "Welcome to Linux AI Assistant!\nType a message or press Ctrl+S to capture the screen.":
            "Bienvenue dans Linux AI Assistant!\nTapez un message ou appuyez sur Ctrl+S pour capturer l'écran.",
        "Settings": "Paramètres",
        "Conversation History": "Historique des conversations",
        "Statistics": "Statistiques",
        "Quit": "Quitter",
        "Name:": "Nom :",
        "Description:": "Description :",
        "Colors": "Couleurs",
        "Background:": "Fond :",
        "Text:": "Texte :",
        "Accent:": "Accent :",
        "Usage Statistics": "Statistiques d'utilisation",
        "Reset Statistics": "Réinitialiser les statistiques",
        "API Configuration": "Configuration de l'API",
        "AI Provider:": "Fournisseur d'IA :",
        "UI Configuration": "Configuration de l'interface",
        "Window opacity:": "Opacité de la fenêtre :",
        "Always visible": "Toujours visible",
        "Theme:": "Thème :",
        "Language:": "Langue :",
        "Manage Themes": "Gérer les thèmes",
        "Docked (reserves screen space)": "Ancré (réserve de l'espace à l'écran)",
        "Dock edge:": "Bord du dock :",
        "Appearance": "Apparence",
        "Custom Themes": "Thèmes personnalisés",
        "Add Theme": "Ajouter un thème",
        "Remove Theme": "Supprimer le thème",
        "Themes": "Thèmes",
        "Features": "Fonctionnalités",
        "Screen capture": "Capture d'écran",
        "OCR (text recognition)": "OCR (reconnaissance de texte)",
        "Expert Mode": "Mode expert",
        "Show Expert Mode button": "Afficher le bouton Mode expert",
        "Right": "Droite",
        "Left": "Gauche",
        "Top": "Haut",
        "Bottom": "Bas",
        "Settings saved successfully": "Paramètres enregistrés avec succès",
        "Settings - Linux AI Assistant": "Paramètres - Linux AI Assistant",
        "Statistics - Linux AI Assistant": "Statistiques - Linux AI Assistant",
        "Conversation History - Linux AI Assistant": "Historique des conversations - Linux AI Assistant",
        "New file": "Nouveau fichier",
        "Proposed changes": "Modifications proposées",
        "Cancel": "Annuler",
        "Write file": "Écrire le fichier",
        "Show Window": "Afficher la fenêtre",
        "Menu": "Menu",
        "Close": "Fermer",
        "Minimize": "Réduire",
        "Processing...": "Traitement en cours...",
        "Run": "Exécuter",
        "Done.": "Terminé.",
        "Failed.": "Échec.",
        "Offline mode: answering from local knowledge.":
            "Mode hors ligne : réponse à partir des connaissances locales.",
        "Run suggested commands?": "Exécuter les commandes suggérées ?",
        "These changes need administrator rights (pkexec):":
            "Ces modifications nécessitent des droits d'administrateur (pkexec) :",
        "Cancelled": "Annulé",
        "Streaming cancelled": "Diffusion annulée",
        "New response received": "Nouvelle réponse reçue",
        "Send (Enter)": "Envoyer (Entrée)",
        "Capture screen (Ctrl+S)": "Capturer l'écran (Ctrl+S)",
        "Expert Mode (Ctrl+E)": "Mode expert (Ctrl+E)",
        "Screen capture is disabled in settings.": "La capture d'écran est désactivée dans les paramètres.",
        "Wait for the current message to be processed": "Attendez le traitement du message en cours",
        "Capturing screen...": "Capture de l'écran...",
        "No theme selected": "Aucun thème sélectionné",
        "Cannot remove built-in themes": "Impossible de supprimer les thèmes intégrés",
        "A theme name is required (letters, digits, '-' or '_', max 64)":
            "Un nom de thème est requis (lettres, chiffres, '-' ou '_', max 64)",
        "Expert Mode ENABLED - Helping with system configuration":
            "Mode expert ACTIVÉ - Aide à la configuration du système",
        "Expert Mode enabled": "Mode expert activé",
        "Expert Mode DISABLED": "Mode expert DÉSACTIVÉ",
        "Expert Mode disabled": "Mode expert désactivé",
        "Hide Window": "Masquer la fenêtre",
        "Path not allowed: {path}": "Chemin non autorisé : {path}",
        "Error: {error}": "Erreur : {error}",
        "Screen captured: {path}": "Écran capturé : {path}",
        "Extracting text from the image...": "Extraction du texte de l'image...",
        "Could not extract text from the image.": "Impossible d'extraire le texte de l'image.",
        "OCR disabled in settings.": "OCR désactivé dans les paramètres.",
        "Error capturing screen: {detail}": "Erreur lors de la capture d'écran : {detail}",
        "(text truncated)": "(texte tronqué)",
        "File written: {path}": "Fichier écrit : {path}",
        "Error writing file: {detail}": "Erreur d'écriture du fichier : {detail}",
        "WARNING: preview truncated at 1 MB; the file is bigger and this diff is NOT complete.":
            "ATTENTION : aperçu tronqué à 1 Mo ; le fichier est plus grand et ce diff N'EST PAS complet.",
        "File changed since the preview was shown; review it again: {path}":
            "Le fichier a changé depuis l'aperçu ; vérifiez-le à nouveau : {path}",
        "API Key:": "Clé API :",
    },
    "de": {
        "Ready": "Bereit",
        "Type your message... (Enter to send)": "Geben Sie Ihre Nachricht ein... (Enter zum Senden)",
        "Enter your API Key": "Geben Sie Ihren API-Key ein",
        "Note: the {var} environment variable overrides this key.":
            "Hinweis: Die Umgebungsvariable {var} überschreibt diesen Schlüssel.",
        "Reload saved keys": "Gespeicherte Schlüssel neu laden",
        "Copy effective key to config": "Effektiven Schlüssel in die Konfiguration kopieren",
        "Key copied to config.json": "Schlüssel in config.json kopiert",
        "User": "Benutzer",
        "AI": "KI",
        "System": "System",
        "Thinking...": "Denke nach...",
        "Welcome to Linux AI Assistant!\nType a message or press Ctrl+S to capture the screen.":
            "Willkommen bei Linux AI Assistant!\nGeben Sie eine Nachricht ein oder drücken Sie Ctrl+S, um den Bildschirm aufzunehmen.",
        "Settings": "Einstellungen",
        "Conversation History": "Unterhaltungsverlauf",
        "Statistics": "Statistiken",
        "Quit": "Beenden",
        "Name:": "Name:",
        "Description:": "Beschreibung:",
        "Colors": "Farben",
        "Background:": "Hintergrund:",
        "Text:": "Text:",
        "Accent:": "Akzent:",
        "Usage Statistics": "Nutzungsstatistiken",
        "Reset Statistics": "Statistiken zurücksetzen",
        "API Configuration": "API-Konfiguration",
        "AI Provider:": "KI-Anbieter:",
        "UI Configuration": "UI-Konfiguration",
        "Window opacity:": "Fensterdeckkraft:",
        "Always visible": "Immer sichtbar",
        "Theme:": "Design:",
        "Language:": "Sprache:",
        "Manage Themes": "Designs verwalten",
        "Docked (reserves screen space)": "Angedockt (reserviert Bildschirmbereich)",
        "Dock edge:": "Dock-Kante:",
        "Appearance": "Erscheinungsbild",
        "Custom Themes": "Eigene Designs",
        "Add Theme": "Design hinzufügen",
        "Remove Theme": "Design entfernen",
        "Themes": "Designs",
        "Features": "Funktionen",
        "Screen capture": "Bildschirmaufnahme",
        "OCR (text recognition)": "OCR (Texterkennung)",
        "Expert Mode": "Expertenmodus",
        "Show Expert Mode button": "Schaltfläche Expertenmodus anzeigen",
        "Right": "Rechts",
        "Left": "Links",
        "Top": "Oben",
        "Bottom": "Unten",
        "Settings saved successfully": "Einstellungen erfolgreich gespeichert",
        "Settings - Linux AI Assistant": "Einstellungen - Linux AI Assistant",
        "Statistics - Linux AI Assistant": "Statistiken - Linux AI Assistant",
        "Conversation History - Linux AI Assistant": "Unterhaltungsverlauf - Linux AI Assistant",
        "New file": "Neue Datei",
        "Proposed changes": "Vorgeschlagene Änderungen",
        "Cancel": "Abbrechen",
        "Write file": "Datei schreiben",
        "Show Window": "Fenster anzeigen",
        "Menu": "Menü",
        "Close": "Schließen",
        "Minimize": "Minimieren",
        "Processing...": "Wird verarbeitet...",
        "Run": "Ausführen",
        "Done.": "Fertig.",
        "Failed.": "Fehlgeschlagen.",
        "Offline mode: answering from local knowledge.":
            "Offline-Modus: Antwort aus lokalem Wissen.",
        "Run suggested commands?": "Vorgeschlagene Befehle ausführen?",
        "These changes need administrator rights (pkexec):":
            "Diese Änderungen benötigen Administratorrechte (pkexec):",
        "Cancelled": "Abgebrochen",
        "Streaming cancelled": "Stream abgebrochen",
        "New response received": "Neue Antwort erhalten",
        "Send (Enter)": "Senden (Enter)",
        "Capture screen (Ctrl+S)": "Bildschirm aufnehmen (Ctrl+S)",
        "Expert Mode (Ctrl+E)": "Expertenmodus (Ctrl+E)",
        "Screen capture is disabled in settings.": "Die Bildschirmaufnahme ist in den Einstellungen deaktiviert.",
        "Wait for the current message to be processed": "Warten, bis die aktuelle Nachricht verarbeitet wurde",
        "Capturing screen...": "Bildschirm wird aufgenommen...",
        "No theme selected": "Kein Design ausgewählt",
        "Cannot remove built-in themes": "Eingebettete Designs können nicht entfernt werden",
        "A theme name is required (letters, digits, '-' or '_', max 64)":
            "Ein Designname erforderlich (Buchstaben, Ziffern, '-' oder '_', max. 64)",
        "Expert Mode ENABLED - Helping with system configuration":
            "Expertenmodus AKTIVIERT - Hilft bei der Systemkonfiguration",
        "Expert Mode enabled": "Expertenmodus aktiviert",
        "Expert Mode DISABLED": "Expertenmodus DEAKTIVIERT",
        "Expert Mode disabled": "Expertenmodus deaktiviert",
        "Hide Window": "Fenster ausblenden",
        "Path not allowed: {path}": "Pfad nicht erlaubt: {path}",
        "Error: {error}": "Fehler: {error}",
        "Screen captured: {path}": "Bildschirmaufnahme erstellt: {path}",
        "Extracting text from the image...": "Text wird aus dem Bild extrahiert...",
        "Could not extract text from the image.": "Text konnte nicht aus dem Bild extrahiert werden.",
        "OCR disabled in settings.": "OCR in den Einstellungen deaktiviert.",
        "Error capturing screen: {detail}": "Fehler beim Erfassen des Bildschirms: {detail}",
        "(text truncated)": "(Text gekürzt)",
        "File written: {path}": "Datei geschrieben: {path}",
        "Error writing file: {detail}": "Fehler beim Schreiben der Datei: {detail}",
        "WARNING: preview truncated at 1 MB; the file is bigger and this diff is NOT complete.":
            "WARNUNG: Vorschau bei 1 MB gekürzt; die Datei ist größer und dieses Diff ist NICHT vollständig.",
        "File changed since the preview was shown; review it again: {path}":
            "Die Datei hat sich seit der Vorschau geändert; bitte erneut prüfen: {path}",
        "API Key:": "API-Schlüssel:",
    },
}

_current_lang = None


def _system_language() -> str:
    """Best-effort system language code ("en" when undeterminable).

    ``locale.getdefaultlocale()`` is deprecated since Python 3.11 and
    removed in 3.13, so prefer ``locale.getlocale()`` (which returns the
    *current* locale - fine here since nothing calls setlocale()) and fall
    back to the LANG/LC_ALL environment variables.
    """
    try:
        lang = locale.getlocale()[0]
        if lang:
            return lang[:2].lower()
    except (TypeError, ValueError):
        pass
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var, "")
        if value and value.upper() not in ("C", "POSIX"):
            return value.split(".")[0].split("_")[0][:2].lower()
    return "en"


def available_languages():
    """Return languages with translations (English always available)."""
    return ["en"] + sorted(TRANSLATIONS.keys())


# Línguas oficialmente suportadas pela aplicação. Qualquer escolha fora
# desta lista (config editada à mão, locale de sistema exótico) recai em
# INGLÊS automaticamente — e o utilizador pode redefinir nas Definições
# (Appearance > Language) para qualquer uma daqui.
SUPPORTED_LANGUAGES = tuple(available_languages())

# Nomes nativos para o seletor de língua nas Definições.
LANGUAGE_NAMES = {
    "en": "English",
    "pt": "Português",
    "es": "Español",
    "fr": "Français",
    "de": "Deutsch",
}


def normalize_language(lang, fallback: str = "en") -> str:
    """Reduce `lang` to a SUPPORTED code, else `fallback` (English).

    "pt-PT", "PT", " it ", "it" não-suportado -> English; None/vazio ->
    `fallback`.
    """
    code = (lang or "")[:2].lower()
    if code in SUPPORTED_LANGUAGES:
        return code
    return fallback


def set_language(lang, config=None):
    """Set the active language. Unknown languages fall back to English.

    `config` (optional) persists the choice to `app.language`; it is kept
    only for backward compatibility and no longer stored in a module global.
    """
    global _current_lang
    # Línguas não suportadas vão para inglês (nunca um estado inválido).
    _current_lang = normalize_language(lang, fallback="en")
    if (lang or "")[:2].lower() != _current_lang and lang:
        import logging
        logging.getLogger(__name__).info(
            "Language %r is not supported; falling back to English", lang
        )
    # Persist every explicit choice, including "en": the old `!= "en"` guard
    # made it impossible to switch back to English from another language.
    if config is not None:
        config.set("app.language", _current_lang)


def set_language_from_config(config):
    """Set the active language from config, defaulting to system locale.

    Config vazia -> língua do sistema SE suportada; caso contrário (ou
    config com valor fora da lista) -> inglês, sempre redefinível.
    """
    global _current_lang
    lang = None
    try:
        lang = config.get("app.language", "")
    except Exception:
        lang = None
    if not lang:
        lang = _system_language()
    # Normalização: sistema/config não suportados -> inglês.
    _current_lang = normalize_language(lang, fallback="en")
    if _current_lang != (lang or "")[:2].lower():
        import logging
        logging.getLogger(__name__).info(
            "Language %r unavailable; using English (changeable in Settings)",
            lang,
        )


def get_language():
    """Return the active language code."""
    return _current_lang or "en"


def _(text):
    """Translate text to the active language; fall back to English."""
    lang = _current_lang or "en"
    if lang == "en":
        return text
    catalog = TRANSLATIONS.get(lang)
    if not catalog:
        return text
    return catalog.get(text, text)


# --------------------------------------------------------------------------
# Offline assistant templates (moved from offline_assistant.py so all
# translation catalogs live in one module; English is the fallback).
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Text templates (English + Portuguese; other languages fall back to English)
# --------------------------------------------------------------------------

OFFLINE_TEXTS = {
    "en": {
        "help": (
            "I am running in offline mode and can help with the local system "
            "({pretty}, {pkg} package manager, {svc} services) without any API. "
            "Try things like:\n"
            "- \"how do I update the system?\"\n"
            "- \"install <package>\"\n"
            "- \"enable service <name>\"\n"
            "- \"set timezone to Europe/Lisbon\"\n"
            "- \"set hostname to laptop\"\n"
            "- \"how much disk/memory do I have?\"\n"
            "- \"network diagnostics\" / \"configure firewall\"\n"
            "- \"where are the configuration files?\" / \"where are the logs?\"\n"
            "- \"change my shell\" / \"add an alias\"\n"
            "- \"clean the cache\" / \"autostart an app\"\n"
        ),
        "reference": "\n\nReference ({wiki_name}): {wiki_url}",
        "config_files": (
            "Configuration file locations on {pretty}:\n\n{body}"
        ),
        "logs": (
            "Logs on {pretty}:\n{logs}\n\nUseful commands:\n{cmds}"
        ),
        "repos": "Repositories on {pretty}:\n{repos}",
        "docs": (
            "Official documentation for {pretty}:\n{body}"
        ),
        "docs_search": "\n\nSearch the {wiki_name} for \"{query}\": {url}",
        "firewall_kb": (
            "Firewall on {pretty}: {tool}\n\nCheck status:\n{status_cmds}\n\n"
            "Example - allow a port:\n  {allow_cmd}"
        ),
        "distro_notes": "\n\nNotes for {pretty}:\n{bullets}",
        "Repositories": "Repositories",
        "Network": "Network",
        "Logs": "Logs",
        "Hostname": "Hostname",
        "Locale": "Locale",
        "Services": "Services",
        "distro": (
            "This system is {pretty} (id: {distro_id}{like}). "
            "Package manager: {pkg}. Service manager: {svc}. Kernel: {kernel}."
        ),
        "update": "To update {pretty}, run:\n{cmds}",
        "install_generic": (
            "To install a package on {pretty}, run:\n{cmd}\n"
            "Replace <package> with the package name, e.g. \"{example}\"."
        ),
        "install_named": "To install '{pkg}', run:\n{cmd}",
        "remove_generic": (
            "To remove a package on {pretty}, run:\n{cmd}\n"
            "Replace <package> with the package name."
        ),
        "remove_named": "To remove '{pkg}', run:\n{cmd}",
        "search_generic": "To search for a package on {pretty}, run:\n{cmd}",
        "search_named": "To search for '{pkg}', run:\n{cmd}",
        "services": (
            "Services on {pretty} are managed with {svc}. Common commands:\n"
            "- list: {list}\n"
            "- status: {status}\n"
            "- enable at boot: {enable}\n"
            "- start now: {start}\n"
            "- restart: {restart}\n"
            "- stop / disable: {stop} / {disable}\n"
            "Replace <name> with the service name."
        ),
        "service_action": "To {action} the service '{svc}', run:\n{cmd}",
        "service_unknown": (
            "I could not find a service name in your message. "
            "Example: \"enable service chronyd\"."
        ),
        "timezone": (
            "On {pretty} ({svc}) the timezone is set with:\n{cmds}\n"
            "Example: \"set timezone to Europe/Lisbon\"."
        ),
        "set_timezone": "To set the timezone to {tz}, run:\n{cmd}",
        "timezone_manual": (
            "Automatic timezone configuration is not available for {svc} here. "
            "Edit /etc/localtime (or /etc/TZ on Alpine) or ask your distribution's docs."
        ),
        "hostname": (
            "On {pretty} ({svc}) the hostname is set with:\n{cmds}\n"
            "Example: \"set hostname to laptop\"."
        ),
        "set_hostname": "To set the hostname to '{host}', run:\n{cmd}",
        "hostname_manual": (
            "Automatic hostname configuration is not available for {svc} here. "
            "Edit /etc/hostname and /etc/hosts, then reboot."
        ),
        "disk": "Disk usage:\n{out}\n\nBiggest consumers:\n{cmds}",
        "disk_none": "Disk usage (run `df -h` yourself):\n{cmds}",
        "memory": "Memory usage:\n{out}",
        "memory_none": "Memory usage (run `free -h` yourself):\n{cmds}",
        "network": (
            "Network diagnostics (run in a terminal):\n"
            "- addresses: ip -brief addr\n"
            "- routes: ip route\n"
            "- DNS: resolvectl status  (or cat /etc/resolv.conf)\n"
            "- connectivity: ping -c 3 1.1.1.1\n"
            "If WiFi is managed by NetworkManager: nmcli device status / nmcli connection show."
        ),
        "firewall": (
            "Firewall on {pretty} ({svc}):\n{cmds}\n"
            "Check what is already active before changing rules."
        ),
        "shell": (
            "To change your default shell:\n"
            "- list installed shells: cat /etc/shells\n"
            "- change: chsh -s /bin/bash  (or /bin/zsh)\n"
            "The change applies to the next login."
        ),
        "alias": (
            "Aliases live in your shell startup file:\n"
            "- Bash: ~/.bashrc   - Zsh: ~/.zshrc\n"
            "Add: alias ll='ls -lah'  then reload it with: source ~/.bashrc"
        ),
        "clean": (
            "To free space on {pretty}:\n{cmds}\n"
            "These commands remove downloaded package caches, not unused packages. "
            "Reinstallation may need Internet access. Inspect application caches individually; "
            "do not delete ~/.cache indiscriminately. Preserve useful logs before pruning them."
        ),
        "autostart": (
            "To start an application automatically at login, create a .desktop "
            "file in ~/.config/autostart/ (e.g. ~/.config/autostart/myapp.desktop):\n"
            "[Desktop Entry]\nType=Application\nName=My app\nExec=/path/to/app"
        ),
    },
    "pt": {
        "help": (
            "Estou em modo offline e posso ajudar com o sistema local "
            "({pretty}, gestor de pacotes {pkg}, serviços {svc}) sem qualquer API. "
            "Experimenta, por exemplo:\n"
            "- \"como atualizo o sistema?\"\n"
            "- \"instalar <pacote>\"\n"
            "- \"ativar serviço <nome>\"\n"
            "- \"definir fuso horário para Europe/Lisbon\"\n"
            "- \"definir hostname como portatil\"\n"
            "- \"quanto espaço/memória tenho?\"\n"
            "- \"diagnóstico de rede\" / \"configurar firewall\"\n"
            "- \"onde estão os ficheiros de configuração?\" / \"onde estão os registos?\"\n"
            "- \"mudar a shell\" / \"adicionar um alias\"\n"
            "- \"limpar a cache\" / \"arrancar app automaticamente\"\n"
        ),
        "reference": "\n\nReferência ({wiki_name}): {wiki_url}",
        "config_files": (
            "Localização dos ficheiros de configuração em {pretty}:\n\n{body}"
        ),
        "logs": (
            "Registos em {pretty}:\n{logs}\n\nComandos úteis:\n{cmds}"
        ),
        "repos": "Repositórios em {pretty}:\n{repos}",
        "docs": (
            "Documentação oficial de {pretty}:\n{body}"
        ),
        "docs_search": "\n\nProcurar \"{query}\" no {wiki_name}: {url}",
        "firewall_kb": (
            "Firewall em {pretty}: {tool}\n\nVerificar estado:\n{status_cmds}\n\n"
            "Exemplo - permitir um porto:\n  {allow_cmd}"
        ),
        "distro_notes": "\n\nNotas sobre {pretty}:\n{bullets}",
        "Repositories": "Repositórios",
        "Network": "Rede",
        "Logs": "Registos",
        "Hostname": "Hostname",
        "Locale": "Locale",
        "Services": "Serviços",
        "distro": (
            "Este sistema é {pretty} (id: {distro_id}{like}). "
            "Gestor de pacotes: {pkg}. Gestor de serviços: {svc}. Kernel: {kernel}."
        ),
        "update": "Para atualizar {pretty}, executa:\n{cmds}",
        "install_generic": (
            "Para instalar um pacote em {pretty}, executa:\n{cmd}\n"
            "Substitui <pacote> pelo nome, por exemplo \"{example}\"."
        ),
        "install_named": "Para instalar '{pkg}', executa:\n{cmd}",
        "remove_generic": (
            "Para remover um pacote em {pretty}, executa:\n{cmd}\n"
            "Substitui <pacote> pelo nome."
        ),
        "remove_named": "Para remover '{pkg}', executa:\n{cmd}",
        "search_generic": "Para procurar um pacote em {pretty}, executa:\n{cmd}",
        "search_named": "Para procurar por '{pkg}', executa:\n{cmd}",
        "services": (
            "Os serviços em {pretty} são geridos com {svc}. Comandos comuns:\n"
            "- listar: {list}\n"
            "- estado: {status}\n"
            "- ativar no arranque: {enable}\n"
            "- iniciar agora: {start}\n"
            "- reiniciar: {restart}\n"
            "- parar / desativar: {stop} / {disable}\n"
            "Substitui <nome> pelo nome do serviço."
        ),
        "service_action": "Para {action} o serviço '{svc}', executa:\n{cmd}",
        "service_unknown": (
            "Não encontrei o nome de um serviço na tua mensagem. "
            "Exemplo: \"ativar serviço chronyd\"."
        ),
        "timezone": (
            "Em {pretty} ({svc}) o fuso horário define-se com:\n{cmds}\n"
            "Exemplo: \"definir fuso horário para Europe/Lisbon\"."
        ),
        "set_timezone": "Para definir o fuso horário {tz}, executa:\n{cmd}",
        "timezone_manual": (
            "A configuração automática de fuso não está disponível para {svc}. "
            "Edita /etc/localtime (ou /etc/TZ no Alpine) ou consulta a documentação."
        ),
        "hostname": (
            "Em {pretty} ({svc}) o hostname define-se com:\n{cmds}\n"
            "Exemplo: \"definir hostname como portatil\"."
        ),
        "set_hostname": "Para definir o hostname '{host}', executa:\n{cmd}",
        "hostname_manual": (
            "A configuração automática de hostname não está disponível para {svc}. "
            "Edita /etc/hostname e /etc/hosts e reinicia."
        ),
        "disk": "Uso de disco:\n{out}\n\nMaiores ocupantes:\n{cmds}",
        "disk_none": "Uso de disco (corre `df -h`):\n{cmds}",
        "memory": "Uso de memória:\n{out}",
        "memory_none": "Uso de memória (corre `free -h`):\n{cmds}",
        "network": (
            "Diagnóstico de rede (corre num terminal):\n"
            "- endereços: ip -brief addr\n"
            "- rotas: ip route\n"
            "- DNS: resolvectl status  (ou cat /etc/resolv.conf)\n"
            "- conectividade: ping -c 3 1.1.1.1\n"
            "Se o WiFi for gerido pelo NetworkManager: nmcli device status / nmcli connection show."
        ),
        "firewall": (
            "Firewall em {pretty} ({svc}):\n{cmds}\n"
            "Vê o que já está ativo antes de alterar regras."
        ),
        "shell": (
            "Para mudar a shell predefinida:\n"
            "- listar instaladas: cat /etc/shells\n"
            "- mudar: chsh -s /bin/bash  (ou /bin/zsh)\n"
            "A alteração aplica-se no próximo login."
        ),
        "alias": (
            "Os aliases ficam no ficheiro de arranque da shell:\n"
            "- Bash: ~/.bashrc   - Zsh: ~/.zshrc\n"
            "Acrescenta: alias ll='ls -lah'  e recarrega com: source ~/.bashrc"
        ),
        "clean": (
            "Para libertar espaço em {pretty}:\n{cmds}\n"
            "Estes comandos removem caches de pacotes descarregados, não pacotes sem uso. "
            "Reinstalar pode exigir Internet. Inspeciona as caches de cada aplicação; "
            "não apagues ~/.cache indiscriminadamente. Guarda os registos úteis antes de reduzir a retenção."
        ),
        "autostart": (
            "Para iniciar uma aplicação automaticamente no login, cria um ficheiro "
            ".desktop em ~/.config/autostart/ (ex.: ~/.config/autostart/myapp.desktop):\n"
            "[Desktop Entry]\nType=Application\nName=A minha app\nExec=/caminho/para/app"
        ),
    },
    "es": {
        "reference": "\n\nReferencia ({wiki_name}): {wiki_url}",
        "config_files": (
            "Ubicación de los archivos de configuración en {pretty}:\n\n{body}"
        ),
        "logs": (
            "Registros en {pretty}:\n{logs}\n\nComandos útiles:\n{cmds}"
        ),
        "repos": "Repositorios en {pretty}:\n{repos}",
        "docs": (
            "Documentación oficial de {pretty}:\n{body}"
        ),
        "docs_search": "\n\nBuscar \"{query}\" en el {wiki_name}: {url}",
        "firewall_kb": (
            "Firewall en {pretty}: {tool}\n\nVerificar estado:\n{status_cmds}\n\n"
            "Ejemplo - permitir un puerto:\n  {allow_cmd}"
        ),
        "distro_notes": "\n\nNotas sobre {pretty}:\n{bullets}",
        "Repositories": "Repositorios",
        "Network": "Red",
        "Logs": "Registros",
        "Hostname": "Nombre de host",
        "Locale": "Locale",
        "Services": "Servicios",
    },
    "fr": {
        "reference": "\n\nRéférence ({wiki_name}) : {wiki_url}",
        "config_files": (
            "Emplacement des fichiers de configuration sur {pretty} :\n\n{body}"
        ),
        "logs": (
            "Journaux sur {pretty} :\n{logs}\n\nCommandes utiles :\n{cmds}"
        ),
        "repos": "Dépôts sur {pretty} :\n{repos}",
        "docs": (
            "Documentation officielle de {pretty} :\n{body}"
        ),
        "docs_search": "\n\nRechercher « {query} » dans le {wiki_name} : {url}",
        "firewall_kb": (
            "Pare-feu sur {pretty} : {tool}\n\nVérifier l'état :\n{status_cmds}\n\n"
            "Exemple - autoriser un port :\n  {allow_cmd}"
        ),
        "distro_notes": "\n\nNotes sur {pretty} :\n{bullets}",
        "Repositories": "Dépôts",
        "Network": "Réseau",
        "Logs": "Journaux",
        "Hostname": "Nom d'hôte",
        "Locale": "Locale",
        "Services": "Services",
    },
    "de": {
        "reference": "\n\nReferenz ({wiki_name}): {wiki_url}",
        "config_files": (
            "Speicherorte der Konfigurationsdateien auf {pretty}:\n\n{body}"
        ),
        "logs": (
            "Protokolle auf {pretty}:\n{logs}\n\nNützliche Befehle:\n{cmds}"
        ),
        "repos": "Paketquellen auf {pretty}:\n{repos}",
        "docs": (
            "Offizielle Dokumentation für {pretty}:\n{body}"
        ),
        "docs_search": "\n\n„{query}“ im {wiki_name} suchen: {url}",
        "firewall_kb": (
            "Firewall auf {pretty}: {tool}\n\nStatus prüfen:\n{status_cmds}\n\n"
            "Beispiel - Port freigeben:\n  {allow_cmd}"
        ),
        "distro_notes": "\n\nHinweise zu {pretty}:\n{bullets}",
        "Repositories": "Paketquellen",
        "Network": "Netzwerk",
        "Logs": "Protokolle",
        "Hostname": "Hostname",
        "Locale": "Locale",
        "Services": "Dienste",
    },
}

# New offline guidance is complete in Portuguese and English. Other UI
# languages explicitly use the existing English fallback for these templates.
OFFLINE_TEXTS["en"].update({
    "negated_action": "Your message contains a negation. I have not proposed any change. For a diagnostic question, request a local guide; for a change, state the exact intended action separately.",
    "ambiguous_action": "The requested change is ambiguous or describes a problem. I have not proposed any command. State one action and the exact targets, or request a local guide.",
    "package_arguments": "Use exact package names separated by spaces, commas or 'and'. I will not guess names or discard the rest of the request.",
    "knowledge_help": "Bundled local guides: 'local guide network', 'search knowledge DNS', or 'guide disk-space'. These guides work without a model or Internet connection.",
    "knowledge_not_found": "No applicable local guide matched. Try network, DNS, disk, memory, permissions, APT or XBPS. Unknown versions and components require confirmation before changes.",
    "knowledge_results": "Local guides found:\n{results}\n\nOpen one with 'guide <identifier>'. Examples are guidance and are never executed from documents.",
    "diagnostic_cancelled": "The diagnostic was cancelled and its local continuation state was cleared.",
    "diagnostic_step": "{title} — step {number}/{total}\n{instruction}",
    "diagnostic_observation": "Local read-only observation:\n{output}",
    "diagnostic_manual": "No automatic observation is available for this step. Run the displayed read command yourself if appropriate, then paste a short result for interpretation.",
    "diagnostic_next": "Paste the result to interpret it and continue, say 'next' for the following step, or 'cancel'. Advancing a step does not confirm the problem is resolved.",
    "diagnostic_sources": "Sources reviewed {date}; not tested on this machine. {sources}",
    "diagnostic_complete": "The guide has finished. Verify the original symptom after any separately approved correction; these observations do not establish a definitive cause.",
    "observed_link_down": "At least one interface reports DOWN. Identify whether it is the intended connection; unused interfaces can normally be down.",
    "observed_no_default": "No IPv4 default route was found in this excerpt. Check the intended connection and IPv6 before concluding that routing is broken.",
    "observed_high_disk": "At least one mount is at or above 90% usage. Identify which mount contains the affected path before choosing what to clean.",
    "observed_high_inodes": "At least one filesystem is at or above 90% inode usage. Investigate the source of many small files instead of deleting unrelated data.",
    "observed_link_local": "A 169.254.x.x address appears. It does not establish that DHCP succeeded; identify the relevant interface and network manager.",
    "observed_inconclusive": "This observation alone does not establish the cause. Compare it with the symptom and the next check.",
    "detected_components": "Release: {version}. Detected executables: {tools}. Package executable confirmed: {pkg_verified}; running service manager confirmed: {svc_verified}. Installed tools are not proof that a service controls the machine.",
    "wifi_offer": (
        "I can list the visible Wi-Fi networks and connect to the one you choose. "
        "The password is typed in a dialog and is not stored in the conversation."
    ),
    "wifi_unavailable": (
        "NetworkManager (nmcli) is not available. I can install {pkg} if you confirm."
    ),
    "printer_offer": (
        "I can look for printers and add a queue for a driverless IPP device. "
        "I will show the exact command before creating the queue."
    ),
    "printer_unavailable": (
        "CUPS (lpinfo) is not available. I can install {pkg} if you confirm."
    ),
    "scanner_offer": (
        "I can look for scanners. If none appear, I can install scanner support if you confirm."
    ),
    "scanner_unavailable": (
        "SANE (scanimage) is not available. I can install {pkg} if you confirm."
    ),
})
OFFLINE_TEXTS["pt"].update({
    "negated_action": "A mensagem contém uma negação. Não propus qualquer alteração. Para diagnosticar, pede um guia local; para alterar, indica separadamente a ação exata que pretendes.",
    "ambiguous_action": "A alteração é ambígua ou a mensagem descreve um problema. Não propus qualquer comando. Indica uma ação e os alvos exatos, ou pede um guia local.",
    "package_arguments": "Usa nomes exatos de pacotes separados por espaços, vírgulas ou 'e'. Não vou adivinhar nomes nem ignorar o resto do pedido.",
    "knowledge_help": "Guias locais incluídos: 'guia local rede', 'pesquisar conhecimento DNS' ou 'guia disk-space'. Funcionam sem modelo e sem ligação à Internet.",
    "knowledge_not_found": "Não encontrei um guia local aplicável. Experimenta rede, DNS, disco, memória, permissões, APT ou XBPS. Versões e componentes desconhecidos exigem confirmação antes de alterar.",
    "knowledge_results": "Guias locais encontrados:\n{results}\n\nAbre um com 'guia <identificador>'. Os exemplos são orientação e nunca são executados a partir de documentos.",
    "diagnostic_cancelled": "O diagnóstico foi cancelado e o seu estado local de continuidade foi apagado.",
    "diagnostic_step": "{title} — passo {number}/{total}\n{instruction}",
    "diagnostic_observation": "Observação local só de leitura:\n{output}",
    "diagnostic_manual": "Este passo não tem uma observação automática disponível. Executa o comando de leitura apresentado, se for adequado, e cola um resultado curto para interpretar.",
    "diagnostic_next": "Cola o resultado para o interpretar e continuar, diz 'e depois?' para o passo seguinte, ou 'cancelar'. Avançar um passo não confirma que o problema ficou resolvido.",
    "diagnostic_sources": "Fontes revistas em {date}; sem teste nesta máquina. {sources}",
    "diagnostic_complete": "O guia terminou. Verifica o sintoma original depois de qualquer correção aprovada separadamente; estas observações não estabelecem uma causa definitiva.",
    "observed_link_down": "Pelo menos uma interface indica DOWN. Confirma se é a ligação pretendida; interfaces não utilizadas podem estar normalmente desligadas.",
    "observed_no_default": "Não encontrei uma rota default IPv4 neste excerto. Confirma a ligação pretendida e IPv6 antes de concluir que existe uma falha de encaminhamento.",
    "observed_high_disk": "Pelo menos uma montagem tem ocupação igual ou superior a 90%. Identifica a montagem do caminho afetado antes de escolher o que limpar.",
    "observed_high_inodes": "Pelo menos um sistema tem ocupação de inodes igual ou superior a 90%. Investiga a origem dos muitos ficheiros pequenos antes de apagar dados.",
    "observed_link_local": "Existe um endereço 169.254.x.x. Não confirma sucesso do DHCP; identifica a interface relevante e o gestor de rede.",
    "observed_inconclusive": "Esta observação isolada não estabelece a causa. Compara-a com o sintoma e a próxima verificação.",
    "detected_components": "Versão: {version}. Executáveis detetados: {tools}. Executável de pacotes confirmado: {pkg_verified}; gestor de serviços em execução confirmado: {svc_verified}. Ter um programa instalado não prova que controle a máquina.",
    "wifi_offer": (
        "Posso listar as redes Wi-Fi visíveis e ligar à que escolheres. "
        "A palavra-passe fica na caixa de diálogo e não entra na conversa."
    ),
    "wifi_unavailable": (
        "O NetworkManager (nmcli) não está disponível. Posso instalar {pkg} se confirmares."
    ),
    "printer_offer": (
        "Posso procurar impressoras e criar uma fila para um aparelho IPP sem driver próprio. "
        "Mostro o comando exato antes de criar a fila."
    ),
    "printer_unavailable": (
        "O CUPS (lpinfo) não está disponível. Posso instalar {pkg} se confirmares."
    ),
    "scanner_offer": (
        "Posso procurar scanners. Se não aparecer nenhum, posso instalar o suporte se confirmares."
    ),
    "scanner_unavailable": (
        "O SANE (scanimage) não está disponível. Posso instalar {pkg} se confirmares."
    ),
})

# Localized verb shown to the user per action.
OFFLINE_SERVICE_ACTIONS = {
    "en": {"enable": "enable", "start": "start", "restart": "restart",
           "stop": "stop", "disable": "disable"},
    "pt": {"enable": "ativar", "start": "iniciar", "restart": "reiniciar",
           "stop": "parar", "disable": "desativar"},
}


def offline_text(lang, key, **kwargs):
    """Format an offline-assistant template in `lang`, English fallback."""
    catalog = OFFLINE_TEXTS.get(lang) or OFFLINE_TEXTS["en"]
    template = catalog.get(key) or OFFLINE_TEXTS["en"][key]
    return template.format(**kwargs) if kwargs else template


def offline_service_action(lang, action):
    """Localized verb for a service action, English fallback."""
    catalog = OFFLINE_SERVICE_ACTIONS.get(lang) or OFFLINE_SERVICE_ACTIONS["en"]
    return catalog[action]


TRANSLATIONS["pt"].update({
    "Keep this display change? [y/N] ": "Manter esta alteração do monitor? [s/N] ",
    "Keep display configuration?": "Manter configuração do monitor?",
    "Revert": "Reverter",
    "Keep": "Manter",
    "Reverting in {seconds} seconds unless you keep this configuration.":
        "A configuração será revertida dentro de {seconds} segundos se não a mantiveres.",
    "Conversations": "Conversas",
    "New conversation": "Nova conversa",
    "Previous conversations": "Histórico anterior",
    "Conversation name": "Nome da conversa",
    "Search all conversations": "Pesquisar em todas as conversas",
    "Archived": "Arquivada",
    "Resume": "Retomar",
    "Rename": "Renomear",
    "Archive / restore": "Arquivar / restaurar",
    "Delete": "Eliminar",
    "Delete this conversation?": "Eliminar esta conversa?",
    "Export conversation": "Exportar conversa",
    "Export Markdown": "Exportar Markdown",
    "Export JSON": "Exportar JSON",
    "Import JSON": "Importar JSON",
    "Import JSON conversation": "Importar conversa JSON",
    "No matches": "Sem resultados",
    "Assistance": "Assistência",
    "Assistance mode": "Modo de assistência",
    "Automatic (local guides if unavailable)": "Automático (guias locais se a IA estiver indisponível)",
    "Local guides (no AI model)": "Guias locais (sem modelo de IA)",
    "Local AI model": "Modelo de IA local",
    "Remote AI provider": "Fornecedor de IA remoto",
    "Local guides available": "Guias locais disponíveis",
    "Provider is not configured": "Fornecedor não configurado",
    "Configured; connection not tested": "Configurado; ligação não testada",
    "Local model ready": "Modelo local disponível",
    "Local server is unavailable": "Servidor local indisponível",
    "Selected model is not installed": "O modelo selecionado não está instalado",
    "Configuration is incompatible with this mode": "Configuração incompatível com este modo",
    "Connection test failed": "O teste de ligação falhou",
    "Local server URL": "Endereço do servidor local",
    "Local model": "Modelo local",
    "Strict local mode (loopback server and local models)": "Modo local estrito (servidor nesta máquina e modelos locais)",
    "Models must already be installed. A localhost address alone does not guarantee local inference.": "Os modelos precisam de estar instalados. Um endereço localhost, por si só, não garante inferência local.",
    "Test connection and list installed models": "Testar ligação e listar modelos instalados",
    "Connection not tested": "Ligação não testada",
    "Testing local connection…": "A testar a ligação local…",
    "Showing the latest 100 messages. Export to view the full conversation.": "A mostrar as últimas 100 mensagens. Exporta para consultar a conversa completa.",
    "Diagnostic report": "Relatório de diagnóstico",
    "File changes": "Alterações a ficheiros",
    "Path": "Caminho",
    "Created": "Criada em",
    "Conversation": "Conversa",
    "Original SHA256": "SHA256 original",
    "Written SHA256": "SHA256 escrito",
    "Original backup": "Cópia original",
    "Recovered": "Reposta em",
    "Recovery backup": "Cópia de recuperação",
    "Describe the symptom": "Descreve o sintoma",
    "Paste a log excerpt (up to 64 KiB)": "Cola um excerto do registo (até 64 KiB)",
    "Select local read checks to collect. No checks are selected by default.": "Seleciona as verificações locais de leitura a recolher. Nenhuma vem selecionada.",
    "Prepare report": "Preparar relatório",
    "Export report": "Exportar relatório",
    "Reports stay on this machine. Review redacted data before sharing; unknown secrets may remain.": "Os relatórios ficam nesta máquina. Revê os dados antes de partilhar; podem restar segredos não reconhecidos.",
    "Diagnostic input exceeds 64 KiB": "A entrada de diagnóstico excede 64 KiB",
    "Preparing local report…": "A preparar o relatório local…",
    "Review the preview before export. Automatic redaction is partial.": "Revê o resultado antes de exportar. A ocultação automática é parcial.",
    "Report exported: {path}": "Relatório exportado: {path}",
    "Show changes from all conversations": "Mostrar alterações de todas as conversas",
    "Include archived file changes": "Incluir alterações de ficheiros arquivadas",
    "Archive": "Arquivo",
    "uncertain": "incerta",
    "recovery_pending": "recuperação pendente",
    "recovery_uncertain": "recuperação incerta",
    "Review recovery": "Rever recuperação",
    "Only approved file writes are recorded. Command effects are not automatically reversible.": "Só são registadas escritas de ficheiros aprovadas. Os efeitos de comandos não são automaticamente reversíveis.",
    "No file changes recorded.": "Sem alterações a ficheiros registadas.",
    "Remove this newly created file and retain a recovery copy.": "Remover este ficheiro criado e conservar uma cópia de recuperação.",
    "Backup exceeds the recovery preview limit (1 MiB). Review it manually.": "A cópia de segurança excede o limite de pré-visualização (1 MiB). Revê-a manualmente.",
    "Confirm file recovery": "Confirmar recuperação do ficheiro",
    "Restore file": "Repor ficheiro",
    "File recovered. Recovery backup: {path}": "Ficheiro reposto. Cópia de recuperação: {path}",
    "Looking up Wi-Fi networks…": "A procurar redes Wi-Fi…",
    "Looking up printers…": "A procurar impressoras…",
    "Looking up scanners…": "A procurar scanners…",
    "Available Wi-Fi networks": "Redes Wi-Fi disponíveis",
    "Password": "Palavra-passe",
    "Connect": "Ligar",
    "The password is sent only to NetworkManager and is not written in the chat.": "A palavra-passe segue só para o NetworkManager e não fica escrita na conversa.",
    "No Wi-Fi networks found.": "Não encontrei redes Wi-Fi.",
    "Connected to {ssid}.": "Ligado a {ssid}.",
    "Could not connect to {ssid}.": "Não foi possível ligar a {ssid}.",
    "Add printer": "Adicionar impressora",
    "Queue name": "Nome da fila",
    "No printers found.": "Não encontrei impressoras.",
    "This device needs a driver. I will not add it automatically.": "Este aparelho precisa de um driver. Não o adiciono automaticamente.",
    "Printer {name} added.": "Impressora {name} adicionada.",
    "Could not add printer {name}.": "Não foi possível adicionar a impressora {name}.",
    "Scanners": "Scanners",
    "No scanners found.": "Não encontrei scanners.",
    "Install scanner support": "Instalar suporte de scanner",
    "Install {pkg}": "Instalar {pkg}",
    "Scanner support installed.": "Suporte de scanner instalado.",
    "Could not install {pkg}.": "Não foi possível instalar {pkg}.",
    "In use": "Em uso",
    "Run these commands? [y/N] ": "Executar estes comandos? [s/N] ",
    "This step needs an interactive terminal.": "Este passo precisa de um terminal interativo.",
    "Number (Enter cancels): ": "Número (Enter cancela): ",
    "Queue name [{name}]: ": "Nome da fila [{name}]: ",
    "Password: ": "Palavra-passe: ",
    "Cancel": "Cancelar",
    "pending": "pendente",
    "applied": "aplicada",
    "restored": "reposta",
    "failed": "falhou",
    "Actions": "Ações",
    "Details": "Detalhes",
    "Status": "Estado",
    "No actions recorded.": "Ainda não há ações registadas.",
    "Cannot read the operation audit.": "Não foi possível ler o registo de ações.",
})


TRANSLATIONS["pt"].update({
    "Trials / Debug": "Ensaios / Debug",
    "Trials / Debug — open case {case}": "Ensaios / Debug — caso aberto {case}",
    "Not specified": "Não especificado",
    "Virtual machine": "Máquina virtual",
    "Physical machine": "Máquina física",
    "WSL smoke test": "Verificação básica em WSL",
    "Simulated fixture": "Cenário simulado",
    "Local collection only. Start a case, test in the main window, then record its result. Closing this panel keeps an open case.":
        "Recolha apenas local. Inicia um caso, testa na janela principal e regista o resultado. Fechar este painel mantém o caso aberto.",
    "Open selected trial": "Abrir ensaio selecionado",
    "Refresh": "Atualizar",
    "Trial title": "Título do ensaio",
    "Environment ID": "ID do ambiente",
    "Build revision": "Revisão da build",
    "Test environment": "Ambiente de ensaio",
    "Start new trial": "Iniciar novo ensaio",
    "Finish trial": "Concluir ensaio",
    "Case ID": "ID do caso",
    "Case variant / attempt": "Variante / tentativa",
    "Operation ID (optional)": "ID da operação (opcional)",
    "Explicit notes only; conversations are not copied automatically.":
        "Só as notas que escreveres são incluídas; as conversas não são copiadas automaticamente.",
    "Begin case": "Iniciar caso",
    "Record result": "Registar resultado",
    "Include bounded app log excerpt (may include other conversations)":
        "Incluir excerto limitado do registo da aplicação (pode conter outras conversas)",
    "Log excerpts and automatic redaction are partial. They may contain sensitive data.":
        "Os excertos do registo e a ocultação automática são parciais. Podem conter dados sensíveis.",
    "Result": "Resultado",
    "Conversation ID": "ID da conversa",
    "Attachment": "Anexo",
    "Type": "Tipo",
    "Add local evidence": "Adicionar evidência local",
    "View attachment": "Ver anexo",
    "Exclude attachment": "Excluir anexo",
    "Choose only relevant text or PNG/JPEG images. Images are not redacted automatically and are never sent to AI by this panel.":
        "Escolhe apenas texto ou imagens PNG/JPEG relevantes. Este painel não oculta automaticamente dados nas imagens nem as envia à IA.",
    "I reviewed the exported content and all attachments": "Revi o conteúdo a exportar e todos os anexos",
    "Refresh export preview": "Atualizar pré-visualização da exportação",
    "Export reviewed ZIP": "Exportar ZIP revisto",
    "Opening local trials…": "A abrir os ensaios locais…",
    "Open case": "Caso aberto",
    "Case {case} is tied to conversation {session}; switching conversations does not change it.":
        "O caso {case} está associado à conversa {session}; mudar de conversa não altera esta associação.",
    "Trial {id} — {state}": "Ensaio {id} — {state}",
    "Finished": "Concluído",
    "Ready to begin a case": "Pronto para iniciar um caso",
    "No trial selected. Start a new trial or open an existing one.":
        "Nenhum ensaio selecionado. Inicia um novo ensaio ou abre um existente.",
    "Review all exported texts below and view every image before confirming export. Nothing is uploaded.":
        "Revê os textos a exportar abaixo e visualiza todas as imagens antes de confirmar a exportação. Os dados ficam locais.",
    "Record the open case result to prepare an export preview.":
        "Regista o resultado do caso aberto para preparar a pré-visualização da exportação.",
    "Rendering export preview…": "A apresentar a pré-visualização da exportação…",
    "Refreshing local preview…": "A atualizar a pré-visualização local…",
    "Opening selected trial…": "A abrir o ensaio selecionado…",
    "Starting local trial…": "A iniciar o ensaio local…",
    "Beginning case…": "A iniciar o caso…",
    "Recording result and local evidence…": "A registar o resultado e a evidência local…",
    "Finishing trial…": "A concluir o ensaio…",
    "Adding private local evidence…": "A adicionar evidência local privada…",
    "Excluding attachment from export…": "A excluir o anexo da exportação…",
    "Opening attachment preview…": "A abrir a pré-visualização do anexo…",
    "Attachment preview": "Pré-visualização do anexo",
    "Scaled image preview. Review the original pixels and image metadata before export; neither is redacted automatically.":
        "Pré-visualização reduzida. Revê os píxeis e metadados da imagem original antes de exportar; estes dados não são ocultados automaticamente.",
    "Exporting reviewed local package…": "A exportar o pacote local revisto…",
    "Trial exported locally: {path}": "Ensaio exportado localmente: {path}",
    "Open": "Abrir",
    "Save": "Guardar",
})

TRANSLATIONS["pt"].update({
    "Message exceeds the local history limit. Shorten it before sending.":
        "A mensagem excede o limite do histórico local. Encurta-a antes de enviar.",
    "Could not save the message: {error}": "Não foi possível guardar a mensagem: {error}",
    "Could not save the response: {error}": "Não foi possível guardar a resposta: {error}",
    "Could not save the operation result: {error}":
        "Não foi possível guardar o resultado da operação: {error}",
    "AI response exceeds the local history limit. The incomplete response was not saved; ask for a shorter response.":
        "A resposta da IA excede o limite do histórico local. A resposta incompleta não foi guardada; pede uma resposta mais curta.",
    "Running confirmed commands…": "A executar os comandos confirmados…",
    "Running confirmed commands. Cancellation stops later commands; an operation already started may still finish.":
        "A executar os comandos confirmados. Cancelar impede os comandos seguintes; uma operação já iniciada pode ainda terminar.",
    "Cancellation requested. An operation already started may still finish; its result will be shown here.":
        "Cancelamento pedido. Uma operação já iniciada pode ainda terminar; o resultado será apresentado aqui.",
    "Operation completed after cancellation. Check its result below; cancellation did not undo it.":
        "A operação terminou depois do cancelamento. Verifica o resultado abaixo; o cancelamento não a desfez.",
    "The operation result could not be determined. Check the target before trying again.":
        "Não foi possível determinar o resultado da operação. Verifica o alvo antes de tentar novamente.",
    "Remote model": "Modelo remoto",
    "List models": "Listar modelos",
    "Listing models…": "A listar os modelos…",
    "Connection not tested": "Ligação não testada",
    "Model listing failed. Check the key, mode and endpoint.":
        "A consulta de modelos falhou. Verifica a chave, o modo e o endereço do fornecedor.",
    "Models listed. Select a model and save to apply it.":
        "Modelos consultados. Seleciona um modelo e guarda para o aplicar.",
    "The API key is unavailable. Check its storage or unlock the key store.":
        "A chave de API está indisponível. Verifica onde está guardada ou desbloqueia o cofre de chaves.",
    "Use stored key (remove empty override from .env)":
        "Usar chave guardada (remover substituição vazia do .env)",
    "The {var} environment variable is empty and blocks the stored API key.":
        "A variável de ambiente {var} está vazia e bloqueia a chave de API guardada.",
    "Use the button below to remove this empty assignment from .env.":
        "Usa o botão abaixo para remover esta atribuição vazia do .env.",
    "Remove or fill this variable at its source, then restart the application.":
        "Remove ou preenche esta variável na sua origem e reinicia a aplicação.",
    "Empty .env override removed. The stored key or edited draft can now be used.":
        "A substituição vazia do .env foi removida. Já podes usar a chave guardada ou a que escreveste.",
    "The empty override could not be removed safely. The stored key was preserved.":
        "Não foi possível remover a substituição vazia com segurança. A chave guardada foi preservada.",
    "Configure a valid API key before listing models.":
        "Configura uma chave de API válida antes de listar os modelos.",
    "The provider rejected the API key (HTTP 401).":
        "O fornecedor rejeitou a chave de API (HTTP 401).",
    "The provider rejected the model-listing request (HTTP 400). Check the API key and endpoint.":
        "O fornecedor rejeitou a consulta de modelos (HTTP 400). Verifica a chave de API e o endereço.",
    "The provider denied access (HTTP 403). Check the API key permissions.":
        "O fornecedor recusou o acesso (HTTP 403). Verifica as permissões da chave de API.",
    "The model-listing endpoint was not found (HTTP 404).":
        "O endereço de consulta de modelos não foi encontrado (HTTP 404).",
    "The provider rate limit or quota was exceeded (HTTP 429). Try again later.":
        "O limite de pedidos ou a quota do fornecedor foi excedido (HTTP 429). Tenta novamente mais tarde.",
    "The provider service is unavailable (HTTP 5xx). Try again later.":
        "O serviço do fornecedor está indisponível (HTTP 5xx). Tenta novamente mais tarde.",
    "Model listing exceeded its deadline.": "A consulta de modelos excedeu o prazo.",
    "Model listing failed; check the connection and provider settings.":
        "A consulta de modelos falhou. Verifica a ligação e as definições do fornecedor.",
    "Enter a valid model identifier.": "Introduz um identificador de modelo válido.",
})

TRANSLATIONS["pt"].update({
    "Preview conversation import": "Pré-visualizar importação da conversa",
    "Import conversation": "Importar conversa",
    "Conversation exports (*.md, *.json)": "Conversas exportadas (*.md, *.json)",
    "Conversation: {title}\nMessages: {count}\nFormat: {format}":
        "Conversa: {title}\nMensagens: {count}\nFormato: {format}",
    "Only conversation text is imported into a new conversation. Actions, tasks and diagnostic progress are not restored.":
        "Só o texto é importado para uma nova conversa. As ações, tarefas e o progresso de diagnóstico não são repostos.",
    "Review this conversation, then repeat the import with --yes.":
        "Revê esta conversa e depois repete a importação com --yes.",
    "Attach an image for the next question": "Anexar uma imagem à próxima pergunta",
    "Capture an image for AI": "Capturar uma imagem para a IA",
    "Choose an image": "Escolher uma imagem",
    "PNG, JPEG and WebP images": "Imagens PNG, JPEG e WebP",
    "Review image for AI": "Rever imagem para a IA",
    "Attach to next request": "Anexar ao próximo pedido",
    "Destination": "Destino",
    "Model": "Modelo",
    "Remove image": "Remover imagem",
    "Describe this image.": "Descreve esta imagem.",
    "Image ready for the next question: {name} ({width} × {height})":
        "Imagem pronta para a próxima pergunta: {name} ({width} × {height})",
    "Could not attach image: {error}": "Não foi possível anexar a imagem: {error}",
    "This client does not support reviewed images.": "Este cliente não suporta o envio de imagens revistas.",
    "Configure a usable OpenRouter provider before sending an image.":
        "Configura um fornecedor OpenRouter disponível antes de enviar uma imagem.",
    "The image request cannot be answered offline.": "O pedido com imagem precisa de um modelo remoto disponível.",
    "The model returned no answer for the image.": "O modelo não devolveu uma resposta para a imagem.",
    "[Image attached for this request; pixels are not stored.]":
        "[Imagem anexada a este pedido; os pixels não são guardados.]",
    "Only the next request will include this image. OpenRouter and the selected model's provider will receive its pixels. Check for private information before attaching. Metadata has been removed; visible secrets have not. The image is not saved in conversation history.":
        "Só o próximo pedido inclui esta imagem. O OpenRouter e o fornecedor do modelo escolhido recebem os pixels. Revê a informação privada antes de anexar. Os metadados foram removidos; os segredos visíveis permanecem. A imagem não é guardada no histórico da conversa.",
    "Choose an image with --image before using --send-image.": "Escolhe uma imagem com --image antes de usar --send-image.",
    "Image: {name} ({width} × {height}); destination: {provider}, {model}.":
        "Imagem: {name} ({width} × {height}); destino: {provider}, {model}.",
    "Review the original image, then repeat with --send-image to send this normalized snapshot.":
        "Revê a imagem original e repete com --send-image para enviar esta cópia preparada.",
    "Enable global shortcut to open the assistant": "Ativar atalho global para abrir o assistente",
    "Global shortcut disabled": "Atalho global desativado",
    "Waiting for desktop shortcut approval": "A aguardar a aprovação do atalho no ambiente gráfico",
    "Global shortcut active through the desktop portal": "Atalho global ativo pelo portal do ambiente gráfico",
    "Global shortcut active on X11": "Atalho global ativo em X11",
    "Global shortcut unavailable. Configure your desktop shortcut to run: linux-ai-assistant --show":
        "Atalho global indisponível. Configura um atalho no ambiente gráfico para executar: linux-ai-assistant --show",
    "Global shortcut unavailable or permission denied. Configure your desktop shortcut to run: linux-ai-assistant --show":
        "Atalho global indisponível ou não autorizado. Configura um atalho no ambiente gráfico para executar: linux-ai-assistant --show",
    "Global shortcut disconnected. Configure your desktop shortcut to run: linux-ai-assistant --show":
        "O atalho global foi desligado. Configura um atalho no ambiente gráfico para executar: linux-ai-assistant --show",
    "Global shortcut unavailable. Configure your desktop shortcut to run: flatpak run io.github.linux_ai_assistant --show":
        "Atalho global indisponível. Configura um atalho no ambiente gráfico para executar: flatpak run io.github.linux_ai_assistant --show",
    "Global shortcut unavailable or permission denied. Configure your desktop shortcut to run: flatpak run io.github.linux_ai_assistant --show":
        "Atalho global indisponível ou não autorizado. Configura um atalho no ambiente gráfico para executar: flatpak run io.github.linux_ai_assistant --show",
    "Global shortcut disconnected. Configure your desktop shortcut to run: flatpak run io.github.linux_ai_assistant --show":
        "O atalho global foi desligado. Configura um atalho no ambiente gráfico para executar: flatpak run io.github.linux_ai_assistant --show",
    "Use selected documents": "Consultar documentos escolhidos",
    "Manage selected documents": "Gerir documentos escolhidos",
    "Local documents": "Documentos locais",
    "Review matching excerpts before sending them to the model. Offline mode shows local matches.":
        "Revê os excertos encontrados antes de os enviar ao modelo. O modo offline mostra resultados locais.",
    "Could not open documents: {error}": "Não foi possível abrir os documentos: {error}",
    "No matching passages in the selected documents. Add documents or refine your question.":
        "Não encontrei excertos relevantes nos documentos escolhidos. Adiciona documentos ou reformula a pergunta.",
    "No matching passages in the selected documents.": "Não encontrei excertos relevantes nos documentos escolhidos.",
    "Review document excerpts": "Rever excertos dos documentos",
    "Use these excerpts": "Usar estes excertos",
    "These excerpts will be sent with your next question to {provider}.":
        "Estes excertos serão enviados com a próxima pergunta para {provider}.",
    "Local document matches (no model request):": "Excertos locais encontrados (sem pedido a um modelo):",
    "The model is unavailable. Local document matches:": "O modelo está indisponível. Excertos locais encontrados:",
    "These local excerpts are not saved in conversation history.": "Estes excertos locais não são guardados no histórico da conversa.",
    "Local document matches were shown. Excerpts remain only in the local document index.":
        "Foram apresentados excertos locais. Os excertos permanecem apenas no índice local de documentos.",
    "Only files you choose are indexed. The index contains private snapshots; source changes are read only when you reindex.":
        "Só são indexados os ficheiros que escolhes. O índice contém cópias privadas; as alterações à origem só são lidas quando atualizas o índice.",
    "Add documents": "Adicionar documentos",
    "Choose local documents": "Escolher documentos locais",
    "UTF-8 text documents": "Documentos de texto UTF-8",
    "Reindex selected document": "Atualizar o documento escolhido",
    "Remove selected document": "Remover o documento escolhido",
    "Clear document index": "Limpar o índice de documentos",
    "Document ID": "ID do documento",
    "Lines": "Linhas",
    "Snapshot imported": "Cópia importada",
    "No documents indexed.": "Não há documentos indexados.",
    "Search works locally. Sending excerpts to an AI provider requires enabling documents for the conversation and reviewing the excerpts before sending.":
        "A pesquisa funciona localmente. Para enviar excertos a um fornecedor de IA, ativa os documentos nesta conversa e revê os excertos antes do envio.",
    "Document snapshots imported. Sources will not refresh automatically.": "Cópias dos documentos importadas. A origem não será atualizada automaticamente.",
    "Selected document snapshot refreshed.": "A cópia do documento escolhido foi atualizada.",
    "Document removed from the local index. The source file was preserved.": "Documento removido do índice local. O ficheiro original foi preservado.",
    "Delete all document snapshots from the local index?": "Eliminar todas as cópias de documentos do índice local?",
    "The original files will be preserved.": "Os ficheiros originais serão preservados.",
    "Document index cleared. Original files were preserved.": "Índice de documentos limpo. Os ficheiros originais foram preservados.",
    "Document snapshot removed.": "Cópia do documento removida.",
    "Document snapshot not found.": "Não encontrei a cópia do documento.",
    "(excerpt truncated)": "(excerto truncado)",
    "All document snapshots removed.": "Todas as cópias de documentos foram removidas.",
    "Use documents clear --yes to remove all local snapshots.": "Usa documents clear --yes para remover todas as cópias locais.",
    "Use --documents before approving document context.": "Usa --documents antes de aprovar o envio dos excertos.",
    "Review these excerpts, then repeat with --send-document-context to send them to the model.":
        "Revê estes excertos e repete com --send-document-context para os enviar ao modelo.",
})


TRANSLATIONS["pt"].update({
    "Recover history": "Recuperar histórico",
    "Recover conversation history": "Recuperar histórico de conversas",
    "Conversation history could not be saved. Recover it before continuing; pending messages have not been discarded.":
        "Não foi possível guardar o histórico. Recupere-o antes de continuar; as mensagens pendentes não foram descartadas.",
    "History recovered. Original backup: {path}": "Histórico recuperado. Cópia do original: {path}",
    "Could not recover history: {error}": "Não foi possível recuperar o histórico: {error}",
    "The conversation history exceeds the size or nesting limit (16 MiB / 128 levels).":
        "O histórico excede o limite de tamanho ou profundidade (16 MiB / 128 níveis).",
    "The conversation history has an invalid or unsupported format.":
        "O histórico de conversas tem um formato inválido ou não suportado.",
    "The conversation history could not be read or saved.": "Não foi possível ler ou guardar o histórico de conversas.",
    "Opening conversation history…": "A abrir o histórico de conversas…",
    "Back up and start new history": "Fazer backup e iniciar novo histórico",
    "The original file will be preserved in a private backup before a new history is created. Unknown formats are not converted. Cancelling keeps the original file unchanged.":
        "O ficheiro original será preservado numa cópia privada antes de criar um novo histórico. Formatos desconhecidos não são convertidos. Cancelar mantém o ficheiro original intacto.",
    "Preserving the original history and starting a new one…": "A preservar o histórico original e a iniciar um novo…",
    "Conversation history recovery failed.": "A recuperação do histórico de conversas falhou.",
    "The original history was preserved at: {path}": "O histórico original foi preservado em: {path}",
    "The history could not be backed up. It was not reset.": "Não foi possível criar a cópia do histórico. O histórico não foi reposto.",
    "File operation completed in another conversation: {message}": "Operação de ficheiro concluída noutra conversa: {message}",
    "Wait for the approved file operation to finish before recovering history.":
        "Aguarde a conclusão da operação de ficheiro aprovada antes de recuperar o histórico.",
    "Writing approved file…": "A escrever o ficheiro aprovado…",
    "Loading file changes…": "A carregar as alterações de ficheiros…",
    "Preparing recovery review…": "A preparar a revisão da recuperação…",
    "Recovering approved file…": "A recuperar o ficheiro aprovado…",
    "Recovery failed: {detail}": "A recuperação falhou: {detail}",
})
