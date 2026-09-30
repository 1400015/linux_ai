import gi
import os
import sys
import time
import threading
from pathlib import Path

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GdkPixbuf', '2.0')

from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Pango


class MainWindow(Gtk.Window):
    """Janela principal da aplicação"""
    
    def __init__(self, app, config_manager, ai_client, system_utils):
        super().__init__(title="Linux AI Assistant")
        
        self.app = app
        self.config = config_manager
        self.ai_client = ai_client
        self.system_utils = system_utils
        
        self.set_default_size(
            config_manager.get("app.width", 400),
            config_manager.get("app.height", 500)
        )
        
        # Posicionar janela
        self.move(
            config_manager.get("app.x_position", 100),
            config_manager.get("app.y_position", 100)
        )
        
        # Configurar transparência
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)
            self.set_opacity(config_manager.get("app.opacity", 0.9))
        
        # Tornar janela sempre visível
        self.set_keep_above(config_manager.get("app.always_on_top", True))
        self.stick()
        
        # Configurar estilo
        self._setup_style()
        
        # Variáveis de estado
        self.expert_mode = False
        self.conversation_history = []
        self.current_response = ""
        self.streaming = False
        
        # Criar interface
        self._create_ui()
        
        # Conectar sinais
        self.connect("delete-event", self.on_delete_event)
        self.connect("configure-event", self.on_configure_event)
        
        # Carregar história de conversa
        self._load_conversation_history()
    
    def _setup_style(self):
        """Configurar estilo CSS da janela"""
        style_provider = Gtk.CssProvider()
        
        css = """
        #main-box {
            background-color: #1e1e1e;
            color: #e0e0e0;
            border-radius: 10px;
            padding: 10px;
        }
        
        #header {
            background-color: #2d2d2d;
            border-radius: 8px 8px 0 0;
            padding: 8px;
            margin-bottom: 10px;
        }
        
        #chat-area {
            background-color: #252525;
            border-radius: 5px;
            padding: 10px;
            margin-bottom: 10px;
        }
        
        #input-area {
            background-color: #2d2d2d;
            border-radius: 5px;
            padding: 10px;
        }
        
        textview, textview.user-message {
            color: #e0e0e0;
            font-family: Monospace;
            font-size: 12pt;
        }
        
        textview.ai-message {
            color: #a0d0a0;
            font-family: Monospace;
            font-size: 12pt;
        }
        
        textview.system-message {
            color: #808080;
            font-family: Monospace;
            font-size: 11pt;
        }
        
        button {
            background-color: #4CAF50;
            color: white;
            border-radius: 5px;
            padding: 5px 10px;
            font-family: Monospace;
            font-size: 10pt;
        }
        
        button:hover {
            background-color: #45a049;
        }
        
        button:active {
            background-color: #3d8b40;
        }
        
        button.expert {
            background-color: #2196F3;
        }
        
        button.expert:hover {
            background-color: #0b7dda;
        }
        
        button.danger {
            background-color: #f44336;
        }
        
        button.danger:hover {
            background-color: #da190b;
        }
        
        entry {
            background-color: #3d3d3d;
            color: #e0e0e0;
            border-radius: 5px;
            padding: 5px;
            font-family: Monospace;
            font-size: 12pt;
        }
        
        scrolledwindow {
            background-color: #252525;
            border-radius: 5px;
        }
        """
        
        style_provider.load_from_data(css.encode())
        Gtk.StyleContext.add_provider_for_screen(
            self.get_screen(),
            style_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
    
    def _create_ui(self):
        """Criar interface da janela"""
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        main_box.set_property("name", "main-box")
        self.add(main_box)
        
        # Header
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        header.set_property("name", "header")
        main_box.pack_start(header, False, False, 0)
        
        # Botão de fechar
        close_btn = Gtk.Button.new_from_icon_name("window-close", Gtk.IconSize.MENU)
        close_btn.connect("clicked", lambda btn: self.on_close_clicked())
        close_btn.set_tooltip_text("Fechar")
        header.pack_end(close_btn, False, False, 0)
        
        # Botão de minimizar
        minimize_btn = Gtk.Button.new_from_icon_name("window-minimize", Gtk.IconSize.MENU)
        minimize_btn.connect("clicked", lambda btn: self.iconify())
        minimize_btn.set_tooltip_text("Minimizar")
        header.pack_end(minimize_btn, False, False, 0)
        
        # Título
        title_label = Gtk.Label(label="Linux AI Assistant")
        title_label.set_halign(Gtk.Align.START)
        title_label.set_valign(Gtk.Align.CENTER)
        header.pack_start(title_label, True, True, 0)
        
        # Área de chat
        chat_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        chat_area.set_property("name", "chat-area")
        main_box.pack_start(chat_area, True, True, 0)
        
        # ScrolledWindow para o chat
        self.chat_scrolled = Gtk.ScrolledWindow()
        self.chat_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        chat_area.pack_start(self.chat_scrolled, True, True, 0)
        
        # TextView para o chat
        self.chat_textview = Gtk.TextView()
        self.chat_textview.set_editable(False)
        self.chat_textview.set_cursor_visible(False)
        self.chat_textview.set_wrap_mode(Gtk.WrapMode.WORD)
        self.chat_textview.set_justification(Gtk.Justification.LEFT)
        
        # Configurar tags para formatação
        text_buffer = self.chat_textview.get_buffer()
        
        user_tag = text_buffer.create_tag("user-message", 
                                           foreground="#e0e0e0",
                                           font="Monospace 12")
        ai_tag = text_buffer.create_tag("ai-message",
                                        foreground="#a0d0a0",
                                        font="Monospace 12")
        system_tag = text_buffer.create_tag("system-message",
                                           foreground="#808080",
                                           font="Monospace 11")
        
        self.chat_textview.set_buffer(text_buffer)
        self.chat_scrolled.add(self.chat_textview)
        
        # Área de input
        input_area = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        input_area.set_property("name", "input-area")
        main_box.pack_start(input_area, False, False, 0)
        
        # Entry para input
        self.input_entry = Gtk.Entry()
        self.input_entry.set_placeholder_text("Escreva a sua mensagem...")
        self.input_entry.connect("activate", self.on_input_activate)
        input_area.pack_start(self.input_entry, True, True, 0)
        
        # Botões de ação
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        input_area.pack_end(button_box, False, False, 0)
        
        # Botão de captura de ecrã
        capture_btn = Gtk.Button(label="📷")
        capture_btn.connect("clicked", self.on_capture_screen_clicked)
        capture_btn.set_tooltip_text("Capturar ecrã")
        button_box.pack_start(capture_btn, False, False, 0)
        
        # Botão de modo especialista
        self.expert_btn = Gtk.Button(label="🧠")
        self.expert_btn.connect("clicked", self.on_expert_mode_toggled)
        self.expert_btn.set_tooltip_text("Modo Especialista")
        self.expert_btn.get_style_context().add_class("expert")
        button_box.pack_start(self.expert_btn, False, False, 0)
        
        # Botão de enviar
        send_btn = Gtk.Button(label="➤")
        send_btn.connect("clicked", lambda btn: self.on_send_clicked())
        send_btn.set_tooltip_text("Enviar")
        button_box.pack_start(send_btn, False, False, 0)
        
        # Adicionar mensagem de boas-vindas
        self._add_system_message("Bem-vindo ao Linux AI Assistant!\nEscreva uma mensagem ou clique em 📷 para capturar o ecrã.")
        
        # Scroll automático para baixo
        self._scroll_to_bottom()
    
    def _add_user_message(self, message: str):
        """Adicionar mensagem do utilizador ao chat"""
        buffer = self.chat_textview.get_buffer()
        end_iter = buffer.get_end_iter()
        
        buffer.insert(end_iter, f"\n[Utilizador]\n{message}\n\n")
        
        # Aplicar tag
        start = buffer.get_iter_at_offset(buffer.get_char_count() - len(message) - 12)
        end = buffer.get_end_iter()
        buffer.apply_tag_by_name("user-message", start, end)
        
        self._scroll_to_bottom()
    
    def _add_ai_message(self, message: str, streaming: bool = False):
        """Adicionar mensagem da IA ao chat"""
        buffer = self.chat_textview.get_buffer()
        
        if streaming and self.streaming:
            # Adicionar a mensagem em stream
            end_iter = buffer.get_end_iter()
            buffer.insert(end_iter, message)
            self._scroll_to_bottom()
        else:
            # Nova mensagem
            end_iter = buffer.get_end_iter()
            buffer.insert(end_iter, f"\n[IA]\n{message}\n\n")
            
            # Aplicar tag
            start = buffer.get_iter_at_offset(buffer.get_char_count() - len(message) - 6)
            end = buffer.get_end_iter()
            buffer.apply_tag_by_name("ai-message", start, end)
            
            self._scroll_to_bottom()
    
    def _add_system_message(self, message: str):
        """Adicionar mensagem do sistema ao chat"""
        buffer = self.chat_textview.get_buffer()
        end_iter = buffer.get_end_iter()
        
        buffer.insert(end_iter, f"\n[Sistema]\n{message}\n\n")
        
        # Aplicar tag
        start = buffer.get_iter_at_offset(buffer.get_char_count() - len(message) - 10)
        end = buffer.get_end_iter()
        buffer.apply_tag_by_name("system-message", start, end)
        
        self._scroll_to_bottom()
    
    def _scroll_to_bottom(self):
        """Scroll automático para o fundo do chat"""
        adjustment = self.chat_scrolled.get_vadjustment()
        adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
    
    def _load_conversation_history(self):
        """Carregar história de conversa"""
        # Por implementar: carregar de ficheiro
        pass
    
    def _save_conversation_history(self):
        """Guardar história de conversa"""
        # Por implementar: guardar em ficheiro
        pass
    
    def on_input_activate(self, entry):
        """Handler para Enter no input"""
        self.on_send_clicked()
    
    def on_send_clicked(self):
        """Handler para clique no botão enviar"""
        text = self.input_entry.get_text().strip()
        if not text:
            return
        
        self.input_entry.set_text("")
        
        # Adicionar mensagem do utilizador
        self._add_user_message(text)
        
        # Adicionar a mensagem à história
        self.conversation_history.append({"role": "user", "content": text})
        
        # Processar em thread separado para não bloquear a UI
        threading.Thread(target=self._process_message, args=(text,), daemon=True).start()
    
    def _process_message(self, message: str):
        """Processar mensagem e obter resposta da IA"""
        GLib.idle_add(self._add_ai_message, "A pensar...")
        
        try:
            # Preparar contexto
            context = self._get_context_message()
            
            # Adicionar contexto à história
            full_history = [context] + self.conversation_history if context else self.conversation_history
            
            # Obter resposta da IA
            self.streaming = True
            self.current_response = ""
            
            response_text = ""
            for chunk in self.ai_client.stream_chat(full_history):
                response_text += chunk
                GLib.idle_add(self._add_ai_message, chunk, True)
            
            self.streaming = False
            
            # Adicionar à história
            self.conversation_history.append({"role": "assistant", "content": response_text})
            
        except Exception as e:
            GLib.idle_add(self._add_system_message, f"Erro: {e}")
    
    def _get_context_message(self) -> Optional[Dict[str, str]]:
        """Obter mensagem de contexto com base no modo"""
        if self.expert_mode:
            return {
                "role": "system",
                "content": """Eres um especialista em sistemas Linux. 
Ajudas o utilizador a resolver problemas, explicar conceitos e fazer alterações a ficheiros de configuração. 
Sê preciso e fornece comandos específicos que o utilizador pode executar. 
Se for necessário editar ficheiros de configuração, pede autorização explícita antes de o fazer. 
Responde em Português de Portugal."""
            }
        else:
            return {
                "role": "system",
                "content": """Eres um assistente de IA útil que responde a perguntas sobre o sistema Linux. 
Podes ajudar com dúvidas gerais, explicações e sugestões. 
Responde em Português de Portugal."""
            }
    
    def on_capture_screen_clicked(self, button):
        """Handler para captura de ecrã"""
        self._add_system_message("A capturar ecrã...")
        
        def capture_and_process():
            try:
                # Capturar ecrã
                success, image_path = self.system_utils.capture_screen()
                
                if success:
                    self._add_system_message(f"Ecrã capturado: {image_path}")
                    
                    # Extrair texto
                    if self.config.get("features.ocr_enabled", True):
                        self._add_system_message("A extrair texto da imagem...")
                        success, text = self.system_utils.extract_text_from_image(image_path)
                        
                        if success and text:
                            # Limitar texto para não sobrecarregar
                            max_length = 2000
                            if len(text) > max_length:
                                text = text[:max_length] + "\n\n... (texto truncado)"
                            
                            self._add_user_message(f"[Captura de ecrã]\n{text}")
                            self.conversation_history.append({
                                "role": "user",
                                "content": f"[Captura de ecrã]\n{text}"
                            })
                        else:
                            self._add_system_message("Não foi possível extrair texto da imagem.")
                    else:
                        self._add_system_message("OCR desativado nas configurações.")
                    
                    # Remover imagem temporária
                    try:
                        os.unlink(image_path)
                    except:
                        pass
                else:
                    self._add_system_message(f"Erro ao capturar ecrã: {image_path}")
                    
            except Exception as e:
                self._add_system_message(f"Erro: {e}")
        
        threading.Thread(target=capture_and_process, daemon=True).start()
    
    def on_expert_mode_toggled(self, button):
        """Alternar modo especialista"""
        self.expert_mode = not self.expert_mode
        
        if self.expert_mode:
            self.expert_btn.get_style_context().add_class("expert")
            self._add_system_message("Modo Especialista ATIVADO - A ajudar com configurações de sistema")
        else:
            self.expert_btn.get_style_context().remove_class("expert")
            self._add_system_message("Modo Especialista DESATIVADO")
        
        # Limpar história para novo contexto
        self.conversation_history = []
    
    def on_close_clicked(self):
        """Handler para fechar janela"""
        self.on_delete_event(None, None)
    
    def on_delete_event(self, widget, event):
        """Handler para fechar janela"""
        # Guardar geometria da janela
        geometry = self.get_window().get_geometry()
        self.config.set_window_geometry(
            geometry.width,
            geometry.height,
            self.get_window().get_position().x,
            self.get_window().get_position().y
        )
        
        # Guardar história
        self._save_conversation_history()
        
        # Fechar aplicação
        Gtk.main_quit()
        return True
    
    def on_configure_event(self, widget, event):
        """Handler para redimensionar/mover janela"""
        # Guardar posição
        x, y = self.get_window().get_position()
        self.config.set("app.x_position", x)
        self.config.set("app.y_position", y)
        
        # Guardar tamanho
        geometry = self.get_window().get_geometry()
        self.config.set("app.width", geometry.width)
        self.config.set("app.height", geometry.height)
        
        return True
