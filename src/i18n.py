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


def set_language(lang, config=None):
    """Set the active language. Unknown languages fall back to English.

    `config` (optional) persists the choice to `app.language`; it is kept
    only for backward compatibility and no longer stored in a module global.
    """
    global _current_lang
    _current_lang = (lang or "en")[:2].lower()
    # Persist every explicit choice, including "en": the old `!= "en"` guard
    # made it impossible to switch back to English from another language.
    if config is not None:
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
        lang = _system_language()
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
            "- \"change my shell\" / \"add an alias\"\n"
            "- \"clean the cache\" / \"autostart an app\"\n"
        ),
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
            "- user cache: rm -rf ~/.cache/*  (safe to delete)\n"
            "- journal (systemd): journalctl --vacuum-size=200M"
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
            "- \"mudar a shell\" / \"adicionar um alias\"\n"
            "- \"limpar a cache\" / \"arrancar app automaticamente\"\n"
        ),
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
            "- cache do utilizador: rm -rf ~/.cache/*  (seguro apagar)\n"
            "- journal (systemd): journalctl --vacuum-size=200M"
        ),
        "autostart": (
            "Para iniciar uma aplicação automaticamente no login, cria um ficheiro "
            ".desktop em ~/.config/autostart/ (ex.: ~/.config/autostart/myapp.desktop):\n"
            "[Desktop Entry]\nType=Application\nName=A minha app\nExec=/caminho/para/app"
        ),
    },
}

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
