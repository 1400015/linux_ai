# Pacote XBPS para Linux AI Assistant (Void Linux / d77void)

Este diretório contém os ficheiros necessários para criar um pacote nativo para **Void Linux** e **d77void** usando o sistema de pacotes XBPS.

## Estrutura

```
xbps-src/
└── linux-ai-assistant/
    ├── template          # Template principal para xbps-src
    └── README.md         # Este ficheiro
```

## Como criar o pacote

### Método 1: Usar xbps-src (recomendado para Void Linux)

1. Copie o template para o diretório de srcpkgs:
   ```bash
   sudo mkdir -p /var/db/xbps/srcpkgs
   sudo cp -r xbps-src/linux-ai-assistant /var/db/xbps/srcpkgs/
   ```

2. Atualize o repositório:
   ```bash
   sudo xbps-install -Su
   ```

3. Instale o pacote:
   ```bash
   sudo xbps-install -S linux-ai-assistant
   ```

### Método 2: Criar manualmente

1. Navegue para o diretório:
   ```bash
   cd xbps-src/linux-ai-assistant
   ```

2. Crie o pacote:
   ```bash
   sudo xbps-create -A linux-ai-assistant
   ```

3. Instale o pacote criado:
   ```bash
   sudo xbps-install -R ./linux-ai-assistant_*.xbps
   ```

## Dependências

O pacote requer as seguintes dependências que serão instaladas automaticamente:

- python3
- python3-gobject
- python3-cairo
- py3-gobject
- py3-cairo
- gtk+3
- scrot
- tesseract-ocr
- tesseract-ocr-por
- tesseract-ocr-eng
- libappindicator-gtk3

## Instalação no d77void

O d77void é uma distribuição baseada em Void Linux, por isso o pacote XBPS funciona perfeitamente.

### Passos para d77void:

1. Baixe o repositório Linux AI Assistant:
   ```bash
   git clone https://github.com/1400015/linux_ai.git
   cd linux_ai
   ```

2. Use o script de instalação específico para Void:
   ```bash
   chmod +x scripts/install_void.sh
   ./scripts/install_void.sh
   ```

3. Ou instale manualmente as dependências:
   ```bash
   sudo xbps-install -Su
   sudo xbps-install -Sy python3 python3-pip python3-venv git scrot tesseract-ocr tesseract-ocr-por tesseract-ocr-eng libgtk-3 libgtk-3-devel py3-gobject py3-cairo gobject-introspection libappindicator-gtk3
   ```

4. Crie o ambiente virtual e instale:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ./run.sh
   ```

## Serviço runit (opcional)

Para integrar com o sistema de init runit do Void Linux:

1. Crie o serviço:
   ```bash
   mkdir -p ~/.local/service/linux-ai-assistant
   ```

2. Crie o ficheiro `run`:
   ```bash
   echo '#!/bin/sh' > ~/.local/service/linux-ai-assistant/run
   echo 'exec /caminho/para/linux_ai/run.sh' >> ~/.local/service/linux-ai-assistant/run
   chmod +x ~/.local/service/linux-ai-assistant/run
   ```

3. Ative o serviço:
   ```bash
   sudo ln -s ~/.local/service/linux-ai-assistant /etc/sv/linux-ai-assistant
   sudo ln -s /etc/sv/linux-ai-assistant /var/service/
   sv up linux-ai-assistant
   ```

## Solução de Problemas

### Problema: xbps não encontra o pacote
- Verifique se o repositório está atualizado: `sudo xbps-install -Su`
- Verifique se o template está no diretório correto: `/var/db/xbps/srcpkgs/`

### Problema: Dependências em falta
- Instale manualmente as dependências listadas acima
- Verifique se o repositório oficial do Void está configurado

### Problema: Erro de permissões
- Execute os comandos com `sudo` quando necessário
- Verifique as permissões dos ficheiros

## Personalização do Template

Para personalizar o template para a sua distribuição baseada em Void:

1. Edite o ficheiro `template`
2. Atualize os campos:
   - `pkgname`: Nome do pacote
   - `version`: Versão do pacote
   - `revision`: Revisão do pacote
   - `maintainer`: Seu nome e email
   - `homepage`: URL do projeto
   - `depends`: Dependências específicas

3. Adicione ou remova ficheiros conforme necessário no `post_install()`

## Recursos

- [Documentação oficial do XBPS](https://github.com/void-linux/void-packages/blob/master/Manual.md)
- [Void Linux Handbook](https://docs.voidlinux.org/)
- [d77void GitHub](https://github.com/d77void)
- [d77void Website](https://d77void.sourceforge.io)
