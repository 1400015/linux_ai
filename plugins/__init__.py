# Linux AI Assistant - Plugins Package
# Este diretório é para plugins que estendem a funcionalidade da aplicação

# ⚠️ Fronteira de confiança: TODOS os ficheiros .py deste diretório são
# importados e executados (register_provider) no arranque da aplicação, com
# os privilégios do utilizador. Nunca deixe este diretório ou os seus
# ficheiros acessíveis a outros utilizadores (grupos/outros sem escrita).

# Para criar um plugin:
# 1. Crie um ficheiro .py neste diretório
# 2. Implemente uma função register_provider(ai_client) para adicionar novos provedores
# 3. Ou implemente outras funcionalidades

# Exemplo de plugin mínimo:
# def register_provider(ai_client):
#     def my_provider_chat(messages, model, api_key, base_url, temperature, max_tokens, timeout):
#         # Implementar lógica para o novo provedor
#         # (a assinatura deve coincidir exatamente com esta)
#         pass
#
#     ai_client.register_provider("my_provider", my_provider_chat)
#     # Opcional, para streaming:
#     # ai_client._stream_my_provider = my_provider_stream

# Notas:
# - O dispatcher chama chat_method(messages, model, api_key, base_url,
#   temperature, max_tokens, timeout) — 7 argumentos posicionais.
# - Um provedor registado sem entrada em api.providers.<nome> em config.json
#   é chamado com api_key=None e base_url=None (e não exige chave).
# - Para streaming, atribua `_stream_<nome>`; a assinatura é a mesma.
# - O plugin será carregado automaticamente ao iniciar a aplicação
