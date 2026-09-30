# Linux AI Assistant - Plugins Package
# Este diretório é para plugins que estendem a funcionalidade da aplicação

# Para criar um plugin:
# 1. Crie um ficheiro .py neste diretório
# 2. Implemente uma função register_provider(ai_client) para adicionar novos provedores
# 3. Ou implemente outras funcionalidades

# Exemplo de plugin mínimo:
# def register_provider(ai_client):
#     def my_provider_chat(messages, model, api_key, temperature, max_tokens, timeout):
#         # Implementar lógica para o novo provedor
#         pass
#     
#     ai_client.register_provider("my_provider", my_provider_chat)

# O plugin será carregado automaticamente ao iniciar a aplicação
