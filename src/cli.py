#!/usr/bin/env python3
"""
Linux AI Assistant - Interface de Linha de Comandos (CLI)

Uso:
    python -m src.cli chat "What is my operating system?"
    python -m src.cli capture --ocr
    python -m src.cli expert "Como configurar o iptables?"
    python -m src.cli system info
    python -m src.cli --help
"""

import sys
import json
import time
import argparse
import logging
from pathlib import Path
from typing import Optional, Dict

# Add src to the path
sys.path.insert(0, str(Path(__file__).parent))

from .config_manager import ConfigManager
from .ai_client import AIClient
from .system_utils import SystemUtils

# Configurar logging for CLI
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class CLIApp:
    """Linux AI Assistant CLI application"""
    
    def __init__(self):
        self.config = ConfigManager()
        self.ai_client = AIClient(self.config)
        self.system_utils = SystemUtils(self.config)
        self.conversation_history = []
        self.expert_mode = False
    
    def parse_args(self):
        """Parse the command-line arguments."""
        parser = argparse.ArgumentParser(
            description='Linux AI Assistant - Interface de Linha de Comandos',
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""
Exemplos:
  %(prog)s chat "What is my operating system?"
  %(prog)s chat --provider openrouter "Usa OpenRouter"
  %(prog)s expert "Como configurar o firewall?"
  %(prog)s capture --ocr
  %(prog)s system info
  %(prog)s system commands
  %(prog)s history
  %(prog)s stats
            """
        )
        
        subparsers = parser.add_subparsers(dest='command', help='Available commands')
        
        # Command: chat
        chat_parser = subparsers.add_parser('chat', help='Chat with the AI')
        chat_parser.add_argument('message', nargs='+', help='Message to send')
        chat_parser.add_argument('--provider', '-p', default=None,
                                help='AI provider (default: from configuration)')
        chat_parser.add_argument('--model', '-m', default=None,
                                help='Model to use')
        chat_parser.add_argument('--expert', '-e', action='store_true',
                                help='Use expert mode')
        chat_parser.add_argument('--stream', '-s', action='store_true',
                                help='Show the response as a stream')
        chat_parser.add_argument('--no-history', action='store_true',
                                help='Do not save to history')
        
        # Command: capture
        capture_parser = subparsers.add_parser('capture', help='Capture the screen')
        capture_parser.add_argument('--ocr', action='store_true',
                                    help='Extract text from the capture')
        capture_parser.add_argument('--output', '-o', default=None,
                                    help='Output file')
        capture_parser.add_argument('--window', '-w', action='store_true',
                                    help='Capture the active window')
        
        # Command: expert
        expert_parser = subparsers.add_parser('expert', help='Expert mode')
        expert_parser.add_argument('message', nargs='+', help='Question for the expert')
        expert_parser.add_argument('--provider', '-p', default=None,
                                    help='AI provider')
        expert_parser.add_argument('--model', '-m', default=None,
                                    help='Model to use')
        
        # Command: system
        system_parser = subparsers.add_parser('system', help='System information')
        system_subparsers = system_parser.add_subparsers(dest='system_command')
        
        # system info
        system_subparsers.add_parser('info', help='Full system information')
        
        # system commands
        system_subparsers.add_parser('commands', help='List of allowed commands')
        
        # system processes
        system_subparsers.add_parser('processes', help='List of processes')
        
        # system network
        system_subparsers.add_parser('network', help='Network information')
        
        # system exec
        exec_parser = system_subparsers.add_parser('exec', help='Run a command')
        exec_parser.add_argument('command', nargs='+', help='Command to run')
        
        # Command: history
        history_parser = subparsers.add_parser('history', help='View conversation history')
        history_parser.add_argument('--limit', '-n', type=int, default=20,
                                     help='Number of messages to show')
        history_parser.add_argument('--clear', action='store_true',
                                     help='Clear history')
        
        # Command: stats
        stats_parser = subparsers.add_parser('stats', help='Usage statistics')
        stats_parser.add_argument('--reset', action='store_true',
                                   help='Reset statistics')
        
        # Command: config
        config_parser = subparsers.add_parser('config', help='Configuration')
        config_subparsers = config_parser.add_subparsers(dest='config_command')
        
        # config list
        config_subparsers.add_parser('list', help='List the current configuration')
        
        # config set
        set_parser = config_subparsers.add_parser('set', help='Set a configuration value')
        set_parser.add_argument('key', help='Configuration key (e.g. api.default_provider)')
        set_parser.add_argument('value', help='Value to set')
        
        # config get
        get_parser = config_subparsers.add_parser('get', help='Get a configuration value')
        get_parser.add_argument('key', help='Configuration key')
        
        # Command: providers
        subparsers.add_parser('providers', help='List the available AI providers')
        
        # Command: tokens
        tokens_parser = subparsers.add_parser('tokens', help='Token management')
        tokens_subparsers = tokens_parser.add_subparsers(dest='tokens_command')
        tokens_subparsers.add_parser('usage', help='Show token usage')
        tokens_subparsers.add_parser('reset', help='Reset the token count')
        
        return parser.parse_args()
    
    def run(self):
        """Run the CLI application."""
        args = self.parse_args()
        
        if not args.command:
            self.print_help()
            return
        
        # Process command
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
        """Show the help text."""
        print("""
Linux AI Assistant - Command-Line Interface

Available commands:
  chat [message]            - Chat with the AI
  capture [options]         - Capture the screen
  expert [message]          - Expert mode
  system [subcommand]       - System information
  history [options]         - View conversation history
  stats [options]           - Usage statistics
  config [subcommand]       - Configuration
  providers                 - List the AI providers
  tokens [subcommand]       - Token management

Use --help with any command for more information.

Examples:
  python -m src.cli chat "What is my operating system?"
  python -m src.cli capture --ocr
  python -m src.cli system info
        """)
    
    def handle_chat(self, args):
        """Process the chat command."""
        message = ' '.join(args.message)
        
        if args.expert:
            self.expert_mode = True
        
        # Prepare context
        context = self._get_context_message()
        full_history = [context] + self.conversation_history if context else list(self.conversation_history)

        # Add the user message
        full_history.append({"role": "user", "content": message})
        
        # Get the response
        if args.stream:
            print("\n[AI] ", end="", flush=True)
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
                print(f"\n[AI]\n{response_text}\n")
            else:
                print("\n[ERROR] Could not get a response\n")
                return
        
        # Save to history
        if not args.no_history:
            self.conversation_history.append({"role": "user", "content": message})
            self.conversation_history.append({"role": "assistant", "content": response_text})
            self._save_history()
    
    def handle_capture(self, args):
        """Process the capture command."""
        if args.window:
            success, image_path = self.system_utils.capture_active_window(args.output)
        else:
            success, image_path = self.system_utils.capture_screen(args.output)
        
        if success:
            print(f"✓ Capture saved at: {image_path}")
            
            if args.ocr:
                print("Extracting text from the image...")
                success, text = self.system_utils.extract_text_from_image(image_path)
                if success:
                    print(f"\n[Screen Capture]\n{text}\n")
                else:
                    print(f"✗ Error extracting text: {text}")
        else:
            print(f"✗ Error while capturing: {image_path}")
    
    def handle_expert(self, args):
        """Process the expert command."""
        self.expert_mode = True
        message = ' '.join(args.message)
        
        # Prepare the expert context
        context = self._get_context_message()
        full_history = [context] + self.conversation_history if context else list(self.conversation_history)
        full_history.append({"role": "user", "content": message})
        
        response_text = self.ai_client.chat(
            full_history,
            provider=args.provider,
            model=args.model
        )
        
        if response_text:
            print(f"\n[Expert]\n{response_text}\n")
        else:
            print("\n[ERROR] Could not get a response\n")
    
    def handle_system(self, args):
        """Process the system command."""
        if not args.system_command:
            # Show the full info
            info = self.system_utils.get_system_info()
            self._print_system_info(info)
            return
        
        if args.system_command == 'info':
            info = self.system_utils.get_system_info()
            self._print_system_info(info)
        elif args.system_command == 'commands':
            commands = self.config.get("permissions.allowed_commands", [])
            print("Allowed commands:")
            for cmd in sorted(commands):
                print(f"  - {cmd}")
        elif args.system_command == 'processes':
            processes = self.system_utils.get_process_list()
            print(f"Processes ({len(processes)}):")
            for proc in processes[:20]:  # Show the first 20
                print(f"  {proc['pid']:>8} {proc['user']:<12} {proc['cpu']:<8} {proc['memory']:<8} {proc['name']}")
            if len(processes) > 20:
                print(f"  ... and {len(processes) - 20} more processes")
        elif args.system_command == 'network':
            network_info = self.system_utils.get_network_info()
            if 'interfaces' in network_info:
                print("Network interfaces:")
                for name, stats in network_info['interfaces'].items():
                    print(f"  {name}:")
                    for key, value in stats.items():
                        print(f"    {key}: {value}")
            if 'connections' in network_info:
                print(f"\nConnections ({len(network_info['connections'])}):")
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
        """Show system information in an organized way."""
        print("\n" + "=" * 50)
        print("SYSTEM INFORMATION")
        print("=" * 50)
        
        print("\nOperating System:")
        print(f"  Name: {info.get('distro', 'Unknown')}")
        print(f"  ID: {info.get('distro_id', 'Unknown')}")
        print(f"  Version: {info.get('release', 'Unknown')}")
        print(f"  Kernel: {info.get('version', 'Unknown')}")
        print(f"  Architecture: {info.get('machine', 'Unknown')} ({info.get('architecture', 'Unknown')})")
        
        print("\nHardware:")
        print(f"  Hostname: {info.get('hostname', 'Unknown')}")
        print(f"  CPU: {info.get('cpu_cores', 'N/A')} cores ({info.get('cpu_physical_cores', 'N/A')} physical)")
        print(f"  CPU usage: {info.get('cpu_usage', 'N/A')}")
        
        print("\nMemory:")
        print(f"  Total: {info.get('memory_total', 'N/A')}")
        print(f"  Used: {info.get('memory_used', 'N/A')}")
        print(f"  Available: {info.get('memory_available', 'N/A')}")
        print(f"  Usage: {info.get('memory_percent', 'N/A')}")
        
        print("\nDisk:")
        print(f"  Total: {info.get('disk_total', 'N/A')}")
        print(f"  Used: {info.get('disk_used', 'N/A')}")
        print(f"  Free: {info.get('disk_free', 'N/A')}")
        print(f"  Usage: {info.get('disk_percent', 'N/A')}")
        
        print(f"\nUptime: {info.get('uptime', 'N/A')}")
        
        print("\nUser:")
        print(f"  Name: {info.get('username', 'Unknown')}")
        print(f"  Is root: {info.get('is_root', False)}")
        
        print("\nGraphical Environment:")
        print(f"  Type: {'Wayland' if info.get('is_wayland') == 'True' else 'X11'}")
        print(f"  Display: {info.get('display', 'N/A')}")
        
        if info.get('is_void'):
            print("\n✓ System: Void Linux")
        if info.get('is_d77void'):
            print("✓ System: d77void")
        
        print("=" * 50 + "\n")
    
    def handle_history(self, args):
        """Process the history command."""
        history_file = Path.home() / ".config" / "linux_ai_assistant" / "history.json"
        
        if args.clear:
            try:
                if history_file.exists():
                    history_file.unlink()
                    print("✓ History cleared")
                else:
                    print("✓ No history to clear")
            except Exception as e:
                print(f"✗ Error clearing history: {e}")
            return
        
        try:
            if history_file.exists():
                with open(history_file, 'r', encoding='utf-8') as f:
                    history = json.load(f)
                
                # Show the last N messages
                limit = min(args.limit, len(history))
                messages = history[-limit:]
                
                print(f"\nLast {len(messages)} history messages:\n")
                for i, msg in enumerate(messages, 1):
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")
                    timestamp = msg.get("timestamp", 0)
                    
                    # Format the timestamp
                    timestamp_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp))
                    
                    # Truncate the content if it is too long
                    if len(content) > 100:
                        content = content[:100] + "..."
                    
                    print(f"{i:>3}. [{timestamp_str}] [{role}]\n   {content}\n")
            else:
                print("✓ No history available")
        except Exception as e:
            print(f"✗ Error loading history: {e}")
    
    def handle_stats(self, args):
        """Process the stats command."""
        token_usage = self.ai_client.get_token_usage()
        
        print("\n" + "=" * 50)
        print("USAGE STATISTICS")
        print("=" * 50 + "\n")
        
        if token_usage:
            print("Token usage by provider:\n")
            for provider, usage in token_usage.items():
                print(f"  {provider}:")
                print(f"    Input tokens:  {usage.get('input', 0):>10}")
                print(f"    Output tokens: {usage.get('output', 0):>10}")
                print(f"    Total tokens:  {usage.get('total', 0):>10}")
                print()
        else:
            print("  No statistics available\n")
        
        if args.reset:
            self.ai_client.reset_token_usage()
            print("✓ Statistics reset")
        
        print("=" * 50 + "\n")
    
    def handle_config(self, args):
        """Process the config command."""
        if not args.config_command:
            # List configuration
            config = self.config.config
            self._print_config(config)
            return
        
        if args.config_command == 'list':
            config = self.config.config
            self._print_config(config)
        elif args.config_command == 'set':
            if not args.key or not args.value:
                print("✗ You must specify both key and value")
                return
            
            # Convert the value
            value = args.value
            if value.lower() in ('true', 'false'):
                value = value.lower() == 'true'
            elif value.isdigit():
                value = int(value)
            elif value.replace('.', '', 1).isdigit():
                value = float(value)
            
            self.config.set(args.key, value)
            self.config.flush()
            # Never return an API key in cleartext to the terminal/shell history
            if "api_key" in args.key.lower():
                shown = "*" * 12
            else:
                shown = value
            print(f"✓ Configuration updated: {args.key} = {shown}")
        elif args.config_command == 'get':
            if not args.key:
                print("✗ You must specify a key")
                return
            value = self.config.get(args.key)
            print(f"{args.key}: {value}")
    
    def _print_config(self, config: Dict, indent: int = 0):
        """Show configuration in an organized way."""
        for key, value in config.items():
            if isinstance(value, dict):
                print("  " * indent + f"{key}:")
                self._print_config(value, indent + 1)
            else:
                # Hide API keys (fixed size: does not reveal the length)
                if 'api_key' in key and value:
                    value = "********"
                print("  " * indent + f"{key}: {value}")
    
    def handle_providers(self, args):
        """Process the providers command."""
        providers = self.ai_client.get_supported_providers()
        
        print("\n" + "=" * 50)
        print("AVAILABLE AI PROVIDERS")
        print("=" * 50 + "\n")
        
        # Get configuration
        api_config = self.config.get("api.providers", {})
        default_provider = self.config.get("api.default_provider", "openrouter")
        
        for provider in providers:
            config = api_config.get(provider, {})
            model = config.get("model", "N/A")
            has_key = bool(config.get("api_key"))
            is_default = provider == default_provider
            
            marker = "✓" if is_default else " "
            key_status = "✓" if has_key else "✗"
            
            print(f"{marker} {provider:<20} Model: {model:<30} Key: {key_status}")
        
        print("\n" + "=" * 50 + "\n")
    
    def handle_tokens(self, args):
        """Process the tokens command."""
        if not args.tokens_command or args.tokens_command == 'usage':
            token_usage = self.ai_client.get_token_usage()
            
            print("\n" + "=" * 50)
            print("TOKEN USAGE")
            print("=" * 50 + "\n")
            
            if token_usage:
                for provider, usage in token_usage.items():
                    print(f"{provider}:")
                    print(f"  Input:  {usage.get('input', 0)}")
                    print(f"  Output: {usage.get('output', 0)}")
                    print(f"  Total:  {usage.get('total', 0)}")
                    print()
            else:
                print("No token statistics available\n")
            
            print("=" * 50 + "\n")
        
        elif args.tokens_command == 'reset':
            self.ai_client.reset_token_usage()
            print("✓ Token count reset")
    
    def _get_context_message(self) -> Optional[Dict[str, str]]:
        """Get the context message based on the current mode."""
        if self.expert_mode:
            return {
                "role": "system",
                "content": """You are the Linux systems expert with extensive knowledge of:
- Configuration of systems and services
- Package management (apt, dnf, pacman, xbps, etc.)
- Network and firewall configuration
- Scripting in Bash and Python
- Troubleshooting common problems
- Performance optimization
- System security

You help the user solve problems, explain concepts and make changes to configuration files.
Be precise and provide specific commands the user can run.
If editing configuration files is needed, ask for explicit authorization before doing so.
Respond in English."""
            }
        else:
            return {
                "role": "system",
                "content": """You are a helpful AI assistant that answers questions about the Linux system and general topics.
You can help with questions, explanations and suggestions.
Respond clearly and concisely in English."""
            }
    
    def _save_history(self):
        """Save the conversation history."""
        history_file = Path.home() / ".config" / "linux_ai_assistant" / "history.json"
        try:
            history_file.parent.mkdir(parents=True, exist_ok=True)
            with open(history_file, 'w', encoding='utf-8') as f:
                json.dump(self.conversation_history, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving history: {e}")


def main():
    """Main entry point for the CLI."""
    try:
        app = CLIApp()
        app.run()
    except KeyboardInterrupt:
        print("\n\n✓ Exiting...")
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
