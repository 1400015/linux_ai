#!/usr/bin/env python3
"""
Linux AI Assistant - Interface de Linha de Comandos (CLI)

Uso:
    python -m src.cli chat "Qual é o meu sistema operativo?"
    python -m src.cli capture --ocr
    python -m src.cli expert "Como configurar o iptables?"
    python -m src.cli system info
    python -m src.cli --help
"""

import sys
import os
import json
import argparse
import logging
from pathlib import Path
from typing import Optional, List, Dict

# Adicionar src ao path
sys.path.insert(0, str(Path(__file__).parent))

from .config_manager import ConfigManager
from .ai_client import AIClient
from .system_utils import SystemUtils

# Configurar logging para CLI
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class CLIApp:
    """Aplicação CLI do Linux AI Assistant"""
    
    def __init__(self):
        self.config = ConfigManager()
        self.ai_client = AIClient(self.config)
        self.system_utils = SystemUtils(self.config)
        self.conversation_history = []
        self.expert_mode = False
    
    def parse_args(self):
        """Parsear argumentos da linha de comandos"""
        parser = argparse.ArgumentParser(
            description='Linux AI Assistant - Interface de Linha de Comandos',
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""
Exemplos:
  %(prog)s chat "Qual é o meu sistema operativo?"
  %(prog)s chat --provider openrouter "Usa OpenRouter"
  %(prog)s expert "Como configurar o firewall?"
  %(prog)s capture --ocr
  %(prog)s system info
  %(prog)s system commands
  %(prog)s history
  %(prog)s stats
            """
        )
        
        subparsers = parser.add_subparsers(dest='command', help='Comandos disponíveis')
        
        # Comando: chat
        chat_parser = subparsers.add_parser('chat', help='Conversar com a IA')
        chat_parser.add_argument('message', nargs='+', help='Mensagem a enviar')
        chat_parser.add_argument('--provider', '-p', default=None, 
                                help='Provedor de IA (default: configuração)')
        chat_parser.add_argument('--model', '-m', default=None,
                                help='Modelo a usar')
        chat_parser.add_argument('--expert', '-e', action='store_true',
                                help='Usar modo especialista')
        chat_parser.add_argument('--stream', '-s', action='store_true',
                                help='Mostrar resposta em stream')
        chat_parser.add_argument('--no-history', action='store_true',
                                help='Não guardar no histórico')
        
        # Comando: capture
        capture_parser = subparsers.add_parser('capture', help='Capturar ecrã')
        capture_parser.add_argument('--ocr', action='store_true',
                                    help='Extrair texto da captura')
        capture_parser.add_argument('--output', '-o', default=None,
                                    help='Ficheiro de saída')
        capture_parser.add_argument('--window', '-w', action='store_true',
                                    help='Capturar janela ativa')
        
        # Comando: expert
        expert_parser = subparsers.add_parser('expert', help='Modo especialista')
        expert_parser.add_argument('message', nargs='+', help='Pergunta para o especialista')
        expert_parser.add_argument('--provider', '-p', default=None,
                                    help='Provedor de IA')
        expert_parser.add_argument('--model', '-m', default=None,
                                    help='Modelo a usar')
        
        # Comando: system
        system_parser = subparsers.add_parser('system', help='Informação do sistema')
        system_subparsers = system_parser.add_subparsers(dest='system_command')
        
        # system info
        system_subparsers.add_parser('info', help='Informação completa do sistema')
        
        # system commands
        commands_parser = system_subparsers.add_parser('commands', 
                                                     help='Lista de comandos permitidos')
        
        # system processes
        system_subparsers.add_parser('processes', help='Lista de processos')
        
        # system network
        system_subparsers.add_parser('network', help='Informação de rede')
        
        # system exec
        exec_parser = system_subparsers.add_parser('exec', help='Executar comando')
        exec_parser.add_argument('command', nargs='+', help='Comando a executar')
        
        # Comando: history
        history_parser = subparsers.add_parser('history', help='Ver histórico de conversas')
        history_parser.add_argument('--limit', '-n', type=int, default=20,
                                     help='Número de mensagens a mostrar')
        history_parser.add_argument('--clear', action='store_true',
                                     help='Limpar histórico')
        
        # Comando: stats
        stats_parser = subparsers.add_parser('stats', help='Estatísticas de uso')
        stats_parser.add_argument('--reset', action='store_true',
                                   help='Resetar estatísticas')
        
        # Comando: config
        config_parser = subparsers.add_parser('config', help='Configuração')
        config_subparsers = config_parser.add_subparsers(dest='config_command')
        
        # config list
        config_subparsers.add_parser('list', help='Listar configuração atual')
        
        # config set
        set_parser = config_subparsers.add_parser('set', help='Definir valor de configuração')
        set_parser.add_argument('key', help='Chave de configuração (ex: api.default_provider)')
        set_parser.add_argument('value', help='Valor a definir')
        
        # config get
        get_parser = config_subparsers.add_parser('get', help='Obter valor de configuração')
        get_parser.add_argument('key', help='Chave de configuração')
        
        # Comando: providers
        subparsers.add_parser('providers', help='Listar provedores de IA disponíveis')
        
        # Comando: tokens
        tokens_parser = subparsers.add_parser('tokens', help='Gestão de tokens')
        tokens_subparsers = tokens_parser.add_subparsers(dest='tokens_command')
        tokens_subparsers.add_parser('usage', help='Mostrar uso de tokens')
        tokens_subparsers.add_parser('reset', help='Resetar contagem de tokens')
        
        return parser.parse_args()
    
    def run(self):
        """Executar a aplicação CLI"""
        args = self.parse_args()
        
        if not args.command:
            self.print_help()
            return
        
        # Processar comando
        if args.command == 'chat':
            self.handle_chat(args)
        elif args.command == 'capture':
            self.handle_capture(args)
        elif args.command == 'expert':
            self.handle_expert(args)
        elif args.command == 'system':
            self.handle_system(args)
        elif args.command == 'history':
            self.handle_history(args)
        elif args.command == 'stats':
            self.handle_stats(args)
        elif args.command == 'config':
            self.handle_config(args)
        elif args.command == 'providers':
            self.handle_providers(args)
        elif args.command == 'tokens':
            self.handle_tokens(args)
        else:
            self.print_help()
    
    def print_help(self):
        """Mostrar ajuda"""
        print("""
Linux AI Assistant - Interface de Linha de Comandos

Comandos disponíveis:
  chat [mensagem]           - Conversar com a IA
  capture [opções]          - Capturar ecrã
  expert [mensagem]        - Modo especialista
  system [subcomando]      - Informação do sistema
  history [opções]          - Ver histórico de conversas
  stats [opções]           - Estatísticas de uso
  config [subcomando]      - Configuração
  providers                - Listar provedores de IA
  tokens [subcomando]      - Gestão de tokens

Use --help com qualquer comando para mais informações.

Exemplos:
  python -m src.cli chat "Qual é o meu sistema operativo?"
  python -m src.cli capture --ocr
  python -m src.cli system info
        """)
    
    def handle_chat(self, args):
        """Processar comando chat"""
        message = ' '.join(args.message)
        
        if args.expert:
            self.expert_mode = True
        
        # Preparar contexto
        context = self._get_context_message()
        full_history = [context] + self.conversation_history if context else self.conversation_history
        
        # Adicionar mensagem do utilizador
        full_history.append({"role": "user", "content": message})
        
        # Obter resposta
        if args.stream:
            print("\n[IA] ", end="", flush=True)
            response_text = ""
            for chunk in self.ai_client.stream_chat(
                full_history, 
                provider=args.provider, 
                model=args.model
            ):
                print(chunk, end="", flush=True)
                response_text += chunk
            print("\n")
        else:
            response_text = self.ai_client.chat(
                full_history,
                provider=args.provider,
                model=args.model
            )
            if response_text:
                print(f"\n[IA]\n{response_text}\n")
            else:
                print("\n[ERRO] Não foi possível obter resposta\n")
                return
        
        # Guardar no histórico
        if not args.no_history:
            self.conversation_history.append({"role": "user", "content": message})
            self.conversation_history.append({"role": "assistant", "content": response_text})
            self._save_history()
    
    def handle_capture(self, args):
        """Processar comando capture"""
        if args.window:
            success, image_path = self.system_utils.capture_active_window(args.output)
        else:
            success, image_path = self.system_utils.capture_screen(args.output)
        
        if success:
            print(f"✓ Captura guardada em: {image_path}")
            
            if args.ocr:
                print("Extraindo texto da imagem...")
                success, text = self.system_utils.extract_text_from_image(image_path)
                if success:
                    print(f"\n[Captura de Ecrã]\n{text}\n")
                else:
                    print(f"✗ Erro ao extrair texto: {text}")
        else:
            print(f"✗ Erro ao capturar: {image_path}")
    
    def handle_expert(self, args):
        """Processar comando expert"""
        self.expert_mode = True
        message = ' '.join(args.message)
        
        # Preparar contexto de especialista
        context = self._get_context_message()
        full_history = [context] + self.conversation_history if context else self.conversation_history
        full_history.append({"role": "user", "content": message})
        
        response_text = self.ai_client.chat(
            full_history,
            provider=args.provider,
            model=args.model
        )
        
        if response_text:
            print(f"\n[Especialista]\n{response_text}\n")
        else:
            print("\n[ERRO] Não foi possível obter resposta\n")
    
    def handle_system(self, args):
        """Processar comando system"""
        if not args.system_command:
            # Mostrar info completa
            info = self.system_utils.get_system_info()
            self._print_system_info(info)
            return
        
        if args.system_command == 'info':
            info = self.system_utils.get_system_info()
            self._print_system_info(info)
        elif args.system_command == 'commands':
            commands = self.config.get("permissions.allowed_commands", [])
            print("Comandos permitidos:")
            for cmd in sorted(commands):
                print(f"  - {cmd}")
        elif args.system_command == 'processes':
            processes = self.system_utils.get_process_list()
            print(f"Processos ({len(processes)}):")
            for proc in processes[:20]:  # Mostrar primeiros 20
                print(f"  {proc['pid']:>8} {proc['user']:<12} {proc['cpu']:<8} {proc['memory']:<8} {proc['name']}")
            if len(processes) > 20:
                print(f"  ... e mais {len(processes) - 20} processos")
        elif args.system_command == 'network':
            network_info = self.system_utils.get_network_info()
            if 'interfaces' in network_info:
                print("Interfaces de rede:")
                for name, stats in network_info['interfaces'].items():
                    print(f"  {name}:")
                    for key, value in stats.items():
                        print(f"    {key}: {value}")
            if 'connections' in network_info:
                print(f"\nConexões ({len(network_info['connections'])}):")
                for conn in network_info['connections'][:10]:
                    print(f"  {conn['local_addr']:<25} -> {conn['remote_addr']:<25} (PID: {conn['pid']})")
        elif args.system_command == 'exec':
            command = ' '.join(args.command)
            success, output = self.system_utils.execute_command(command)
            if success:
                print(f"✓ {output}")
            else:
                print(f"✗ {output}")
    
    def _print_system_info(self, info: Dict):
        """Mostrar informação do sistema de forma organizada"""
        print("\n" + "=" * 50)
        print("INFORMAÇÃO DO SISTEMA")
        print("=" * 50)
        
        print(f"\nSistema Operativo:")
        print(f"  Nome: {info.get('distro', 'Unknown')}")
        print(f"  ID: {info.get('distro_id', 'Unknown')}")
        print(f"  Versão: {info.get('release', 'Unknown')}")
        print(f"  Kernel: {info.get('version', 'Unknown')}")
        print(f"  Arquitetura: {info.get('machine', 'Unknown')} ({info.get('architecture', 'Unknown')})")
        
        print(f"\nHardware:")
        print(f"  Hostname: {info.get('hostname', 'Unknown')}")
        print(f"  CPU: {info.get('cpu_cores', 'N/A')} núcleos ({info.get('cpu_physical_cores', 'N/A')} físicos)")
        print(f"  Uso de CPU: {info.get('cpu_usage', 'N/A')}")
        
        print(f"\nMemória:")
        print(f"  Total: {info.get('memory_total', 'N/A')}")
        print(f"  Usada: {info.get('memory_used', 'N/A')}")
        print(f"  Disponível: {info.get('memory_available', 'N/A')}")
        print(f"  Uso: {info.get('memory_percent', 'N/A')}")
        
        print(f"\nDisco:")
        print(f"  Total: {info.get('disk_total', 'N/A')}")
        print(f"  Usado: {info.get('disk_used', 'N/A')}")
        print(f"  Livre: {info.get('disk_free', 'N/A')}")
        print(f"  Uso: {info.get('disk_percent', 'N/A')}")
        
        print(f"\nUptime: {info.get('uptime', 'N/A')}")
        
        print(f"\nUtilizador:")
        print(f"  Nome: {info.get('username', 'Unknown')}")
        print(f"  É root: {info.get('is_root', False)}")
        
        print(f"\nAmbiente Gráfico:")
        print(f"  Tipo: {'Wayland' if info.get('is_wayland') == 'True' else 'X11'}")
        print(f"  Display: {info.get('display', 'N/A')}")
        
        if info.get('is_void'):
            print(f"\n✓ Sistema: Void Linux")
        if info.get('is_d77void'):
            print(f"✓ Sistema: d77void")
        
        print("=" * 50 + "\n")
    
    def handle_history(self, args):
        """Processar comando history"""
        history_file = Path.home() / ".config" / "linux_ai_assistant" / "history.json"
        
        if args.clear:
            try:
                if history_file.exists():
                    history_file.unlink()
                    print("✓ Histórico limpo")
                else:
                    print("✓ Não há histórico para limpar")
            except Exception as e:
                print(f"✗ Erro ao limpar histórico: {e}")
            return
        
        try:
            if history_file.exists():
                with open(history_file, 'r', encoding='utf-8') as f:
                    history = json.load(f)
                
                # Mostrar últimas N mensagens
                limit = min(args.limit, len(history))
                messages = history[-limit:]
                
                print(f"\nÚltimas {len(messages)} mensagens do histórico:\n")
                for i, msg in enumerate(messages, 1):
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")
                    timestamp = msg.get("timestamp", 0)
                    
                    # Formatar timestamp
                    timestamp_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp))
                    
                    # Truncar conteúdo se for muito longo
                    if len(content) > 100:
                        content = content[:100] + "..."
                    
                    print(f"{i:>3}. [{timestamp_str}] [{role}]\n   {content}\n")
            else:
                print("✓ Não há histórico disponível")
        except Exception as e:
            print(f"✗ Erro ao carregar histórico: {e}")
    
    def handle_stats(self, args):
        """Processar comando stats"""
        token_usage = self.ai_client.get_token_usage()
        
        print("\n" + "=" * 50)
        print("ESTATÍSTICAS DE USO")
        print("=" * 50 + "\n")
        
        if token_usage:
            print("Uso de tokens por provedor:\n")
            for provider, usage in token_usage.items():
                print(f"  {provider}:")
                print(f"    Input tokens:  {usage.get('input', 0):>10}")
                print(f"    Output tokens: {usage.get('output', 0):>10}")
                print(f"    Total tokens:  {usage.get('total', 0):>10}")
                print()
        else:
            print("  Não há estatísticas disponíveis\n")
        
        if args.reset:
            self.ai_client.reset_token_usage()
            print("✓ Estatísticas resetadas")
        
        print("=" * 50 + "\n")
    
    def handle_config(self, args):
        """Processar comando config"""
        if not args.config_command:
            # Listar configuração
            config = self.config.config
            self._print_config(config)
            return
        
        if args.config_command == 'list':
            config = self.config.config
            self._print_config(config)
        elif args.config_command == 'set':
            if not args.key or not args.value:
                print("✗ É necessário especificar key e value")
                return
            
            # Converter valor
            value = args.value
            if value.lower() in ('true', 'false'):
                value = value.lower() == 'true'
            elif value.isdigit():
                value = int(value)
            elif value.replace('.', '', 1).isdigit():
                value = float(value)
            
            self.config.set(args.key, value)
            print(f"✓ Configuração atualizada: {args.key} = {value}")
        elif args.config_command == 'get':
            if not args.key:
                print("✗ É necessário especificar key")
                return
            value = self.config.get(args.key)
            print(f"{args.key}: {value}")
    
    def _print_config(self, config: Dict, indent: int = 0):
        """Mostrar configuração de forma organizada"""
        for key, value in config.items():
            if isinstance(value, dict):
                print("  " * indent + f"{key}:")
                self._print_config(value, indent + 1)
            else:
                # Esconder API keys
                if 'api_key' in key and value:
                    value = "*" * len(value)
                print("  " * indent + f"{key}: {value}")
    
    def handle_providers(self, args):
        """Processar comando providers"""
        providers = self.ai_client.get_supported_providers()
        
        print("\n" + "=" * 50)
        print("PROVEDORES DE IA DISPONÍVEIS")
        print("=" * 50 + "\n")
        
        # Obter configurações
        api_config = self.config.get("api.providers", {})
        default_provider = self.config.get("api.default_provider", "openrouter")
        
        for provider in providers:
            config = api_config.get(provider, {})
            model = config.get("model", "N/A")
            has_key = bool(config.get("api_key"))
            is_default = provider == default_provider
            
            marker = "✓" if is_default else " "
            key_status = "✓" if has_key else "✗"
            
            print(f"{marker} {provider:<20} Modelo: {model:<30} Key: {key_status}")
        
        print("\n" + "=" * 50 + "\n")
    
    def handle_tokens(self, args):
        """Processar comando tokens"""
        if not args.tokens_command or args.tokens_command == 'usage':
            token_usage = self.ai_client.get_token_usage()
            
            print("\n" + "=" * 50)
            print("USO DE TOKENS")
            print("=" * 50 + "\n")
            
            if token_usage:
                for provider, usage in token_usage.items():
                    print(f"{provider}:")
                    print(f"  Input:  {usage.get('input', 0)}")
                    print(f"  Output: {usage.get('output', 0)}")
                    print(f"  Total:  {usage.get('total', 0)}")
                    print()
            else:
                print("Não há estatísticas de tokens disponíveis\n")
            
            print("=" * 50 + "\n")
        
        elif args.tokens_command == 'reset':
            self.ai_client.reset_token_usage()
            print("✓ Contagem de tokens resetada")
    
    def _get_context_message(self) -> Optional[Dict[str, str]]:
        """Obter mensagem de contexto com base no modo"""
        if self.expert_mode:
            return {
                "role": "system",
                "content": """Eres um especialista em sistemas Linux com vastos conhecimentos sobre:
- Configuração de sistemas e serviços
- Gestão de pacotes (apt, dnf, pacman, xbps, etc.)
- Configuração de rede e firewall
- Scripting em Bash e Python
- Resolução de problemas comuns
- Otimização de performance
- Segurança do sistema

Ajudas o utilizador a resolver problemas, explicar conceitos e fazer alterações a ficheiros de configuração.
Sê preciso e fornece comandos específicos que o utilizador pode executar.
Se for necessário editar ficheiros de configuração, pede autorização explícita antes de o fazer.
Responde em Português de Portugal."""
            }
        else:
            return {
                "role": "system",
                "content": """Eres um assistente de IA útil que responde a perguntas sobre o sistema Linux e assuntos gerais.
Podes ajudar com dúvidas, explicações e sugestões.
Responde em Português de Portugal de forma clara e concisa."""
            }
    
    def _save_history(self):
        """Guardar histórico de conversas"""
        history_file = Path.home() / ".config" / "linux_ai_assistant" / "history.json"
        try:
            history_file.parent.mkdir(parents=True, exist_ok=True)
            with open(history_file, 'w', encoding='utf-8') as f:
                json.dump(self.conversation_history, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Erro a guardar histórico: {e}")


def main():
    """Ponto de entrada principal para CLI"""
    import time
    
    try:
        app = CLIApp()
        app.run()
    except KeyboardInterrupt:
        print("\n\n✓ Sair...")
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ Erro: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
