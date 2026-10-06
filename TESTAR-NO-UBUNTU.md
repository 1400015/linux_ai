# Testar a interface e a captura de ecrã no Ubuntu

Esta cópia contém a base 1.4.1 com as correções de contraste, ícones,
entrada de mensagens, abertura do gestor de documentos e captura Wayland
pelo portal do sistema. É uma versão de teste; ainda não foi integrada no
`master`.

O ambiente do relato é Ubuntu 26.04.1 LTS, amd64, VMware, com sessão
Wayland. A interface foi verificada com GTK/Adwaita em Xvfb e o protocolo
de captura com um portal simulado num barramento D-Bus real. O objetivo
deste teste é confirmar o resultado na tua sessão Ubuntu/GNOME.

Esta cópia também corrige a configuração online e a consulta de modelos,
incluindo atribuições vazias do `.env` criadas por instaladores anteriores.

## Abrir a cópia nova

1. Fecha completamente a aplicação antiga, usando **Ctrl+Q** na janela
   principal ou **Sair/Quit** no menu. Se ficar aberta, a nova execução
   apenas apresenta a instância antiga.
2. Extrai o ZIP para uma **pasta nova**, sem substituir a pasta anterior.
3. Dentro da pasta extraída, onde estão `run.sh` e `requirements.txt`,
   abre um terminal e executa:

   ```bash
   python3 -m venv --system-site-packages venv
   ./venv/bin/python -m pip install -r requirements.txt
   bash run.sh --show
   ```

As dependências GTK do sistema já devem estar disponíveis, pois a
aplicação anterior funciona nessa máquina. A instalação via `pip`
necessita de internet. O comando `--system-site-packages` permite ao
ambiente virtual usar os bindings GTK instalados no Ubuntu.

A cópia nova usa a tua configuração e o histórico existentes. Não
é necessário reinstalar o atalho, modificar o `.env` ou apagar dados.
Durante este teste, abre-a com o comando acima; o atalho antigo continua
a apontar para a pasta anterior.

## O que verificar

- Os ícones e as legendas ficam visíveis, incluindo nos temas claro e escuro.
- A caixa de escrita ocupa uma linha inteira, com os botões abaixo.
- Consegues escrever `ajuda`, enviar com Enter e ler a resposta offline.
- **Documentos locais** mostra os botões de adicionar, reindexar, remover e
  limpar, mesmo com o índice vazio. Fecha esse diálogo para voltar ao chat.
- **Ensaios / Debug** mostra campos e legendas legíveis.
- Carrega no botão da câmara ou usa **Ctrl+S**: no Ubuntu/Wayland, o sistema
  apresenta a seleção ou autorização de captura. Confirma-a para extrair
  texto com OCR. Cancelar nessa caixa ou na aplicação deve interromper o
  pedido, sem tentar outra ferramenta.

Se aparecer **Wayland screenshot portal unavailable**, verifica as dependências
do desktop no terminal do Ubuntu:

```bash
sudo apt install xdg-desktop-portal xdg-desktop-portal-gnome
```

Depois termina e volta a iniciar a sessão gráfica. Para extrair texto, o
Tesseract e os idiomas `por`/`eng` também devem estar instalados; a ausência de
texto na imagem é diferente de uma falha de captura.

Para voltar à cópia anterior, fecha esta aplicação e abre o `run.sh` da
pasta anterior. Nos resultados do teste, indica a referência do download
e se o problema apareceu no tema claro, escuro ou em ambos.

## Escolher um modelo online

1. Abre **Configurações → API** e escolhe Google ou Mistral.
2. Se o aviso disser que uma variável está vazia e bloqueia a chave,
   carrega em **Usar chave guardada (remover substituição vazia do .env)**.
   Esta ação retira apenas a linha vazia indicada. Se o botão estiver
   desativado, segue a instrução do aviso para corrigir a variável na origem.
3. Introduz a chave no campo **API Key**, ou conserva a chave já guardada.
4. Carrega em **Listar modelos**, abre a lista de **Modelo remoto** e
   seleciona o modelo que queres usar. A consulta usa a chave escrita sem
   a guardar antes de confirmares.
5. Carrega em **Aceitar** para guardar o fornecedor, a chave editada e o
   modelo. Na janela principal, envia uma mensagem para testar a resposta.

Em **Assistência**, usa o modo automático ou remoto. Os modos local e
offline impedem os pedidos a estes fornecedores. Se o fornecedor recusar
a consulta, o aviso distingue chave rejeitada, permissões, quota e
indisponibilidade do serviço.
