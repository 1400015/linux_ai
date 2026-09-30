# Linux AI Assistant

Um assistente de IA permanente para Linux com interface flutuante, integração com várias APIs de IA, captura de ecrã, modo especialista e mais.

![Linux AI Assistant](assets/screenshot.png)

[![Void Linux](https://img.shields.io/badge/Void%20Linux-Compatible-green)](https://voidlinux.org)
[![d77void](https://img.shields.io/badge/d77void-Supported-blue)](https://d77void.sourceforge.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Funcionalidades

- ✅ **Interface flutuante transparente** - Janela permanente com fundos pretos e transparência configurável
- ✅ **Integração com múltiplas APIs** - Suporte para OpenRouter, Google AI Studio e modelos locais
- ✅ **Captura de ecrã com OCR** - Captura o ecrã e extrai texto automaticamente
- ✅ **Modo Especialista** - Assistente especializado em sistemas Linux
- ✅ **Edição de ficheiros** - Permite editar ficheiros de configuração com autorização
- ✅ **Execução de comandos** - Executar comandos do sistema com permissões controladas
- ✅ **Histórico de conversa** - Mantém o contexto da conversa
- ✅ **Ícone de system tray** - Acesso rápido através do ícone na barra de tarefas
- ✅ **Inicialização automática** - Configurável para iniciar com o sistema

## Requisitos

### Sistema
- Linux (testado em Ubuntu, Fedora, Debian, Arch, **Void Linux**, **d77void**)
- Python 3.8+
- GTK 3.0+

### Dependências do Sistema
```bash
# Ubuntu/Debian
sudo apt-get install python3 python3-pip python3-venv git scrot tesseract-ocr tesseract-ocr-por tesseract-ocr-eng libgtk-3-0 python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-appindicator3-0.1

# Fedora
sudo dnf install python3 python3-pip git scrot tesseract tesseract-langpack-por tesseract-langpack-eng gtk3 python3-gobject

# Arch
sudo pacman -S python python-pip git scrot tesseract tesseract-data-por tesseract-data-eng gtk3 python-gobject
```

### Dependências Python
Ver [requirements.txt](requirements.txt)

## Instalação

### Método 1: Instalação Automática

```bash
# Clonar o repositório
git clone https://github.com/1400015/linux_ai.git
cd linux_ai

# Tornar scripts executáveis
chmod +x scripts/*.sh

# Executar instalação (para a maioria das distribuições)
./scripts/install.sh

# Para Void Linux e d77void específicamente
./scripts/install_void.sh
```

### Método 2: Instalação Manual

```bash
# Clonar o repositório
git clone https://github.com/1400015/linux_ai.git
cd linux_ai

# Criar ambiente virtual
python3 -m venv venv
source venv/bin/activate

# Instalar dependências
pip install -r requirements.txt

# Criar ficheiro de configuração
mkdir -p ~/.config/linux_ai_assistant
cp config/config.json ~/.config/linux_ai_assistant/
cp config/.env.example ~/.config/linux_ai_assistant/.env

# Editar configuração
nano ~/.config/linux_ai_assistant/.env

# Executar
python src/app.py
```

### Para Void Linux e d77void

```bash
# Instalar dependências com xbps
git clone https://github.com/1400015/linux_ai.git
cd linux_ai

# Instalar dependências do sistema
sudo xbps-install -Su
sudo xbps-install -Sy python3 python3-pip python3-venv git scrot tesseract-ocr tesseract-ocr-por tesseract-ocr-eng libgtk-3 libgtk-3-devel py3-gobject py3-cairo gobject-introspection libappindicator-gtk3

# Criar ambiente virtual e instalar
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Executar
./run.sh
```

## Configuração

### API Keys

Edite o ficheiro `~/.config/linux_ai_assistant/.env` e adicione as suas API Keys:

```ini
OPENROUTER_API_KEY=your_openrouter_api_key_here
GOOGLE_AI_STUDIO_KEY=your_google_ai_studio_key_here
```

Pode obter API Keys gratuitas em:
- [OpenRouter](https://openrouter.ai/) - $0.001 por 1K tokens (grátis para testar)
- [Google AI Studio](https://aistudio.google.com/) - Grátis com quotas generosas

### Configuração da Aplicação

Edite o ficheiro `~/.config/linux_ai_assistant/config.json` para personalizar:

- **Dimensões e posição da janela**
- **Opacidade** (0.1 - 1.0)
- **Provedor de IA padrão**
- **Modelo de IA**
- **Funcionalidades ativadas**
- **Permissões**
- **Cores do tema**

### Provedores Suportados

| Provedor | Base URL | Modelo Padrão |
|----------|----------|----------------|
| OpenRouter | https://openrouter.ai/api/v1 | google/gemini-flash-1.5 |
| Google AI Studio | https://generativelanguage.googleapis.com/v1beta | gemini-1.5-flash |
| Modelo Local | http://localhost:11434/v1 | llama3.2 |

## Uso

### Executar a Aplicação

```bash
# Usando o script de execução
./run.sh

# Ou diretamente
source venv/bin/activate
python src/app.py
```

### Atalhos

| Ação | Atalho |
|------|--------|
| Enviar mensagem | Enter |
| Capturar ecrã | Botão 📷 |
| Modo Especialista | Botão 🧠 |
| Fechar janela | Botão X |

### Modo Especialista

Quando o modo especialista está ativo:
- A IA responde como especialista em sistemas Linux
- Pode ajudar com configurações do sistema
- Pode sugerir comandos específicos
- Pode editar ficheiros de configuração (com autorização)

### Captura de Ecrã

1. Clique no botão 📷
2. O ecrã será capturado automaticamente
3. O texto será extraído usando OCR
4. O texto será adicionado à conversa

### Edição de Ficheiros

No modo especialista, pode pedir à IA para:
- Ler ficheiros de configuração
- Explicar opções de configuração
- Sugerir alterações
- Editar ficheiros (com autorização explícita)

## Inicialização Automática

Para configurar a aplicação para iniciar automaticamente:

```bash
# Adicionar à inicialização
./scripts/autostart.sh enable

# Remover da inicialização
./scripts/autostart.sh disable

# Verificar estado
./scripts/autostart.sh check
```

## Desinstalação

```bash
./scripts/uninstall.sh
```

## Estrutura do Projeto

```
linux_ai_assistant/
├── src/
│   ├── __init__.py
│   ├── app.py              # Aplicação principal
│   ├── ai_client.py        # Cliente de APIs de IA
│   ├── config_manager.py   # Gestor de configuração
│   ├── main_window.py      # Janela principal
│   ├── system_utils.py     # Utilitários do sistema
│   └── tray_icon.py        # Ícone de system tray
├── config/
│   ├── config.json         # Configuração default
│   └── .env.example         # Exemplo de variáveis de ambiente
├── scripts/
│   ├── install.sh          # Script de instalação
│   ├── uninstall.sh        # Script de desinstalação
│   └── autostart.sh        # Configuração de inicialização
├── assets/
│   └── icon.png            # Ícone da aplicação
├── requirements.txt        # Dependências Python
└── README.md               # Documentação
```

## Suporte para d77void e Void Linux

O Linux AI Assistant tem suporte completo para **Void Linux** e **d77void**:

### 🎯 Funcionalidades Específicas

- ✅ **Suporte nativo para XBPS** - Gestor de pacotes do Void Linux
- ✅ **Integração com runit** - Sistema de init do Void (em vez de systemd)
- ✅ **Script de instalação dedicado** - `install_void.sh` otimizado para Void/d77void
- ✅ **Pacote XBPS** - Template disponível em `xbps-src/` para criar pacote nativo
- ✅ **Deteção automática** - Reconhece Void Linux e d77void automaticamente

### 📦 Instalação no d77void

O d77void é uma distribuição baseada em Void Linux com vários Window Managers pré-configurados. O Linux AI Assistant funciona perfeitamente em todas as variantes do d77void:

- **Awesome WM**
- **BSPWM**
- **DWM**
- **Fluxbox**
- **Hyprland**
- **i3**
- **JWM**
- **LabWC**
- **LeftWM**
- **MangoWC**
- **Niri**
- **Openbox**
- **Qtile**
- **River**
- **Sway**
- **Wayfire**
- **wmd77**
- **GNOME**
- **LXQt**
- **Plasma**
- **XFCE**

### 🔧 Serviço runit (Opcional)

Para integrar o Linux AI Assistant com o sistema de init **runit** do Void Linux:

```bash
# Criar diretório de serviço
mkdir -p ~/.local/service/linux-ai-assistant

# Criar ficheiro run
cat > ~/.local/service/linux-ai-assistant/run <<EOL
#!/bin/sh
exec /caminho/para/linux_ai/run.sh
EOL

chmod +x ~/.local/service/linux-ai-assistant/run

# Ativar serviço (requer sudo)
sudo ln -s ~/.local/service/linux-ai-assistant /etc/sv/linux-ai-assistant
sudo ln -s /etc/sv/linux-ai-assistant /var/service/

# Gerir serviço
sv up linux-ai-assistant    # Iniciar
sv down linux-ai-assistant  # Parar
sv restart linux-ai-assistant  # Reiniciar
```

### 📦 Criar Pacote XBPS

Para criar um pacote nativo para Void Linux:

```bash
# Copiar template para srcpkgs
sudo cp -r xbps-src/linux-ai-assistant /var/db/xbps/srcpkgs/

# Atualizar repositório
sudo xbps-install -Su

# Instalar pacote
sudo xbps-install -S linux-ai-assistant
```

## Personalização

### Adicionar Novos Provedores

Edite `src/ai_client.py` e adicione um novo método para o provedor:

```python
def _chat_novo_provedor(self, messages, model, api_key, temperature, max_tokens, timeout):
    # Implementar lógica para o novo provedor
    pass
```

Depois adicione o provedor ao método `chat`:

```python
elif provider == "novo_provedor":
    return self._chat_novo_provedor(messages, model, api_key, temperature, max_tokens, timeout)
```

### Adicionar Novos Comandos Permitidos

Edite `~/.config/linux_ai_assistant/config.json`:

```json
"permissions": {
    "require_sudo": true,
    "allowed_commands": [
        "ls", "cat", "grep", "novo_comando"
    ],
    "allowed_edit_dirs": [
        "/etc", "/home", "/usr/local", "/novo/diretorio"
    ]
}
```

## Resolução de Problemas

### Problema: Janela não aparece
- Verifique se o GTK está instalado corretamente
- Tente executar com: `GTK_DEBUG=interactive python src/app.py`

### Problema: OCR não funciona
- Instale o Tesseract: `sudo apt-get install tesseract-ocr tesseract-ocr-por tesseract-ocr-eng`
- Instale o pytesseract: `pip install pytesseract`

### Problema: API não responde
- Verifique a sua API Key
- Verifique a sua quota
- Teste a API manualmente com curl

### Problema: Falta permissões
- Execute com `sudo` se necessário
- Verifique as permissões em `config.json`

## Contribuir

1. Faça fork do projeto
2. Crie uma branch para a sua feature (`git checkout -b feature/nova-feature`)
3. Faça commit das suas alterações (`git commit -m 'Adicionar nova feature'`)
4. Faça push para a branch (`git push origin feature/nova-feature`)
5. Abra um Pull Request

## Licença

MIT License - veja [LICENSE](LICENSE) para mais detalhes.

## Agradecimentos

- [OpenRouter](https://openrouter.ai/) - API de IA
- [Google AI Studio](https://aistudio.google.com/) - API de IA
- [GTK](https://www.gtk.org/) - Interface gráfica
- [Tesseract](https://github.com/tesseract-ocr/tesseract) - OCR

---

**Feito com ❤️ para a comunidade Linux**
