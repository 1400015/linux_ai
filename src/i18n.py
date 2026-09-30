"""Internationalization support with English fallback.

Texts are authored in English and translated via the CATALOG dict.
If a language (or a specific text) has no translation, the English
string is returned. The active language is read from the
``app.language`` config key and can be changed with :func:`set_language`.
"""

import locale

TRANSLATIONS = {
    "pt": {
        "Ready": "Pronto",
        "Type your message... (Ctrl+Enter to send)": "Escreva a sua mensagem... (Ctrl+Enter para enviar)",
        "Enter your API Key": "Insira a sua API Key",
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
    },
    "es": {
        "Ready": "Listo",
        "Type your message... (Ctrl+Enter to send)": "Escribe tu mensaje... (Ctrl+Enter para enviar)",
        "Enter your API Key": "Introduce tu API Key",
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
    },
    "fr": {
        "Ready": "Prêt",
        "Type your message... (Ctrl+Enter to send)": "Tapez votre message... (Ctrl+Entrée pour envoyer)",
        "Enter your API Key": "Saisissez votre clé API",
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
    },
    "de": {
        "Ready": "Bereit",
        "Type your message... (Ctrl+Enter to send)": "Geben Sie Ihre Nachricht ein... (Ctrl+Enter zum Senden)",
        "Enter your API Key": "Geben Sie Ihren API-Key ein",
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
    },
}

_current_lang = None
_config = None


def available_languages():
    """Return languages with translations (English always available)."""
    return ["en"] + sorted(TRANSLATIONS.keys())


def set_language(lang, config=None):
    """Set the active language. Unknown languages fall back to English."""
    global _current_lang, _config
    _current_lang = (lang or "en")[:2].lower()
    _config = config
    if config is not None and _current_lang != "en":
        config.set("app.language", _current_lang)


def set_language_from_config(config):
    """Set the active language from config, defaulting to system locale."""
    global _current_lang
    lang = None
    try:
        lang = config.get("app.language", "")
    except Exception:
        lang = None
    if not lang:
        sys_lang = locale.getdefaultlocale()[0] or "en"
        lang = sys_lang[:2].lower()
    _current_lang = lang[:2].lower()


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
