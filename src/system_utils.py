import os
import shlex
import subprocess
import platform
import time
from typing import Dict, List, Tuple, Any
from pathlib import Path
import logging

# Configurar logger
logger = logging.getLogger(__name__)


class SystemUtils:
    """Utilitários para interagir com o sistema Linux"""
    
    def __init__(self, config_manager):
        self.config = config_manager
        self.allowed_commands = set(config_manager.get("permissions.allowed_commands", []))
        self.allowed_edit_dirs = set(config_manager.get("permissions.allowed_edit_dirs", []))
        self._detect_environment()
    
    def _detect_environment(self):
        """Detetar ambiente (X11 vs Wayland) e outras propriedades do sistema"""
        self.is_wayland = os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
        self.is_x11 = os.environ.get("DISPLAY") is not None
        self.is_root = os.geteuid() == 0
        self.username = os.getlogin()
        
        logger.info(f"Ambiente detetado: {'Wayland' if self.is_wayland else 'X11'}")
        logger.info(f"Utilizador: {self.username}, Root: {self.is_root}")
    
    def _sanitize_command(self, command: str) -> List[str]:
        """Sanitizar comando para evitar injeção de comandos"""
        try:
            # Usar shlex para split seguro
            return shlex.split(command)
        except ValueError as e:
            logger.error(f"Comando inválido para sanitizar: {command} - {e}")
            return []
    
    def _validate_path(self, path: str) -> bool:
        """Validar se um path é permitido"""
        try:
            # Normalizar path
            path = os.path.normpath(path)
            path = os.path.abspath(path)
            
            # Verificar se está em diretórios permitidos
            for allowed_dir in self.allowed_edit_dirs:
                allowed_dir = os.path.normpath(allowed_dir)
                if path.startswith(allowed_dir):
                    return True
            
            logger.warning(f"Path não permitido: {path}")
            return False
        except Exception as e:
            logger.error(f"Erro a validar path: {e}")
            return False
    
    def get_system_info(self) -> Dict[str, str]:
        """Obter informação do sistema"""
        info = {}
        
        try:
            # Informação básica
            info["os"] = platform.system()
            info["release"] = platform.release()
            info["version"] = platform.version()
            info["machine"] = platform.machine()
            info["processor"] = platform.processor()
            info["architecture"] = platform.architecture()[0]
            
            # Detetar se é Void Linux ou d77void
            info["is_void"] = False
            info["is_d77void"] = False
            try:
                with open('/etc/os-release', 'r') as f:
                    for line in f:
                        if line.startswith('ID='):
                            info["distro_id"] = line.split('=')[1].strip().strip('"')
                            if info["distro_id"] == "void":
                                info["is_void"] = True
                        elif line.startswith('D77VOID_VERSION='):
                            info["is_d77void"] = True
            except Exception as e:
                logger.warning(f"Erro a ler /etc/os-release: {e}")
            
            # Memória
            try:
                import psutil
                mem = psutil.virtual_memory()
                info["memory_total"] = f"{mem.total / (1024**3):.2f} GB"
                info["memory_used"] = f"{mem.used / (1024**3):.2f} GB"
                info["memory_percent"] = f"{mem.percent}%"
                info["memory_available"] = f"{mem.available / (1024**3):.2f} GB"
            except ImportError:
                # Fallback sem psutil
                try:
                    with open('/proc/meminfo', 'r') as f:
                        for line in f:
                            if line.startswith('MemTotal:'):
                                total_kb = int(line.split()[1])
                                info["memory_total"] = f"{total_kb / (1024**2):.2f} GB"
                            elif line.startswith('MemFree:'):
                                free_kb = int(line.split()[1])
                                info["memory_available"] = f"{free_kb / (1024**2):.2f} GB"
                except Exception as e:
                    logger.warning(f"Não foi possível obter info de memória: {e}")
            
            # CPU
            try:
                import psutil
                info["cpu_cores"] = str(psutil.cpu_count(logical=True))
                info["cpu_physical_cores"] = str(psutil.cpu_count(logical=False))
                info["cpu_usage"] = f"{psutil.cpu_percent(interval=1)}%"
                info["cpu_freq"] = f"{psutil.cpu_freq().current:.2f} MHz" if hasattr(psutil.cpu_freq(), 'current') else "N/A"
            except ImportError:
                try:
                    with open('/proc/cpuinfo', 'r') as f:
                        cores = 0
                        for line in f:
                            if line.startswith('processor'):
                                cores += 1
                        info["cpu_cores"] = str(cores)
                except Exception as e:
                    logger.warning(f"Não foi possível obter info de CPU: {e}")
            
            # Disco
            try:
                import psutil
                disk = psutil.disk_usage('/')
                info["disk_total"] = f"{disk.total / (1024**3):.2f} GB"
                info["disk_used"] = f"{disk.used / (1024**3):.2f} GB"
                info["disk_percent"] = f"{disk.percent}%"
                info["disk_free"] = f"{disk.free / (1024**3):.2f} GB"
            except ImportError:
                try:
                    stat = os.statvfs('/')
                    info["disk_total"] = f"{(stat.f_blocks * stat.f_frsize) / (1024**3):.2f} GB"
                    info["disk_free"] = f"{(stat.f_bavail * stat.f_frsize) / (1024**3):.2f} GB"
                except Exception as e:
                    logger.warning(f"Não foi possível obter info de disco: {e}")
            
            # Uptime
            try:
                import psutil
                uptime_seconds = int(time.time() - psutil.boot_time())
            except ImportError:
                try:
                    with open('/proc/uptime', 'r') as f:
                        uptime_seconds = int(float(f.readline().split()[0]))
                except Exception as e:
                    logger.warning(f"Não foi possível obter uptime: {e}")
                    uptime_seconds = 0
            
            days, remainder = divmod(uptime_seconds, 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, seconds = divmod(remainder, 60)
            info["uptime"] = f"{days}d {hours}h {minutes}m {seconds}s"
            info["uptime_seconds"] = str(uptime_seconds)
            
            # Utilizador
            try:
                info["username"] = os.getlogin()
            except:
                info["username"] = os.environ.get("USER", "unknown")
            
            info["hostname"] = platform.node()
            
            # Ambiente gráfico
            info["is_wayland"] = str(self.is_wayland)
            info["is_x11"] = str(self.is_x11)
            info["display"] = os.environ.get("DISPLAY", "N/A")
            info["xdg_session_type"] = os.environ.get("XDG_SESSION_TYPE", "N/A")
            
            # Distribuição Linux
            try:
                with open('/etc/os-release', 'r') as f:
                    for line in f:
                        if line.startswith('PRETTY_NAME='):
                            info["distro"] = line.split('=')[1].strip().strip('"')
                            break
                    else:
                        info["distro"] = "Unknown"
            except:
                info["distro"] = "Unknown"
            
            return info
        except Exception as e:
            logger.error(f"Erro a obter info do sistema: {e}")
            return {"error": str(e)}
    
    def execute_command(self, command: str, timeout: int = 10) -> Tuple[bool, str]:
        """
        Executar um comando no sistema com sanitização
        
        Args:
            command: Comando a executar
            timeout: Timeout em segundos
            
        Returns:
            Tuplo (sucesso, output)
        """
        if not command or not isinstance(command, str):
            logger.error("Comando inválido")
            return False, "Comando inválido"
        
        # Sanitizar comando
        try:
            cmd_parts = self._sanitize_command(command)
            if not cmd_parts:
                return False, "Comando inválido após sanitização"
            
            cmd_base = cmd_parts[0]
        except Exception as e:
            logger.error(f"Erro a sanitizar comando: {e}")
            return False, f"Erro a processar comando: {e}"
        
        # Verificar se o comando é permitido
        if cmd_base not in self.allowed_commands:
            logger.warning(f"Comando não permitido: {cmd_base}")
            return False, f"Comando não permitido: {cmd_base}"
        
        try:
            logger.info(f"A executar comando: {' '.join(cmd_parts)}")
            
            result = subprocess.run(
                cmd_parts,  # Usar lista em vez de shell=True para segurança
                capture_output=True,
                text=True,
                timeout=timeout
            )
            
            if result.returncode == 0:
                logger.debug(f"Comando executado com sucesso: {cmd_base}")
                return True, result.stdout
            else:
                logger.warning(f"Comando falhou ({result.returncode}): {cmd_base}")
                return False, result.stderr or result.stdout
                
        except subprocess.TimeoutExpired:
            logger.error(f"Timeout ao executar comando: {command}")
            return False, f"Timeout ao executar comando: {command}"
        except Exception as e:
            logger.error(f"Erro ao executar comando: {e}")
            return False, f"Erro ao executar comando: {e}"
    
    def execute_sudo_command(self, command: str, password: str = None, timeout: int = 10) -> Tuple[bool, str]:
        """
        Executar comando com privilégios sudo
        
        Args:
            command: Comando a executar
            password: Password para sudo (opcional)
            timeout: Timeout em segundos
            
        Returns:
            Tuplo (sucesso, output)
        """
        if not command or not isinstance(command, str):
            return False, "Comando inválido"
        
        # Sanitizar comando
        try:
            cmd_parts = self._sanitize_command(command)
            if not cmd_parts:
                return False, "Comando inválido após sanitização"
            
            cmd_base = cmd_parts[0]
        except Exception as e:
            return False, f"Erro a processar comando: {e}"
        
        # Verificar se o comando é permitido
        if cmd_base not in self.allowed_commands:
            return False, f"Comando não permitido: {cmd_base}"
        
        try:
            if password:
                # Usar sudo com password
                cmd = ['sudo', '-S'] + cmd_parts
                result = subprocess.run(
                    cmd,
                    input=password + '\n',
                    capture_output=True,
                    text=True,
                    timeout=timeout
                )
            else:
                # Pedir password interativamente
                cmd = ['sudo'] + cmd_parts
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout
                )
            
            if result.returncode == 0:
                return True, result.stdout
            else:
                return False, result.stderr or result.stdout
                
        except subprocess.TimeoutExpired:
            return False, f"Timeout ao executar comando sudo: {command}"
        except Exception as e:
            return False, f"Erro ao executar comando sudo: {e}"
    
    def read_file(self, filepath: str, max_lines: int = 100) -> Tuple[bool, str]:
        """
        Ler ficheiro do sistema
        
        Args:
            filepath: Caminho do ficheiro
            max_lines: Número máximo de linhas a ler
            
        Returns:
            Tuplo (sucesso, conteúdo)
        """
        if not filepath or not isinstance(filepath, str):
            return False, "Caminho inválido"
        
        # Validar path
        if not self._validate_path(filepath):
            logger.warning(f"Acesso negado ao ficheiro: {filepath}")
            return False, f"Acesso negado ao ficheiro: {filepath}"
        
        try:
            file_path = Path(filepath)
            if not file_path.exists():
                return False, f"Ficheiro não encontrado: {filepath}"
            
            # Verificar se é diretório
            if file_path.is_dir():
                return False, f"É um diretório: {filepath}"
            
            # Verificar permissões
            if not os.access(filepath, os.R_OK):
                return False, f"Sem permissão para ler: {filepath}"
            
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = []
                for i, line in enumerate(f):
                    if i >= max_lines:
                        lines.append(f"\n... (mais {max_lines} linhas omitidas)")
                        break
                    lines.append(line)
            
            return True, ''.join(lines)
        except PermissionError:
            logger.error(f"Permissão negada para ler: {filepath}")
            return False, f"Permissão negada para ler: {filepath}"
        except Exception as e:
            logger.error(f"Erro ao ler ficheiro: {e}")
            return False, f"Erro ao ler ficheiro: {e}"
    
    def write_file(self, filepath: str, content: str, append: bool = False) -> Tuple[bool, str]:
        """
        Escrever em ficheiro do sistema
        
        Args:
            filepath: Caminho do ficheiro
            content: Conteúdo a escrever
            append: Se True, adiciona ao ficheiro; caso contrário, substitui
            
        Returns:
            Tuplo (sucesso, mensagem)
        """
        if not filepath or not isinstance(filepath, str):
            return False, "Caminho inválido"
        
        if not content or not isinstance(content, str):
            return False, "Conteúdo inválido"
        
        # Validar path
        if not self._validate_path(filepath):
            logger.warning(f"Acesso negado ao ficheiro: {filepath}")
            return False, f"Acesso negado ao ficheiro: {filepath}"
        
        try:
            file_path = Path(filepath)
            
            # Verificar permissões
            if not os.access(file_path.parent, os.W_OK):
                return False, f"Sem permissão para escrever em: {file_path.parent}"
            
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            mode = 'a' if append else 'w'
            with open(file_path, mode, encoding='utf-8') as f:
                f.write(content)
            
            logger.info(f"Ficheiro {filepath} {'atualizado' if not append else 'adicionado'} com sucesso")
            return True, f"Ficheiro {filepath} {'atualizado' if not append else 'adicionado'} com sucesso"
        except PermissionError:
            logger.error(f"Permissão negada para escrever: {filepath}")
            return False, f"Permissão negada para escrever: {filepath}"
        except Exception as e:
            logger.error(f"Erro ao escrever ficheiro: {e}")
            return False, f"Erro ao escrever ficheiro: {e}"
    
    def capture_screen(self, output_path: str = None) -> Tuple[bool, str]:
        """
        Capturar ecrã com suporte para Wayland e X11
        
        Args:
            output_path: Caminho para guardar a captura (opcional)
            
        Returns:
            Tuplo (sucesso, caminho_da_imagem)
        """
        try:
            if self.is_wayland:
                # Tentar usar grim para Wayland
                if subprocess.run(['which', 'grim'], capture_output=True).returncode == 0:
                    if output_path:
                        result = subprocess.run(['grim', '-o', output_path], 
                                              capture_output=True, timeout=10)
                        if result.returncode == 0:
                            logger.info(f"Ecrã capturado com grim: {output_path}")
                            return True, output_path
                    else:
                        import tempfile
                        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                            result = subprocess.run(['grim', '-o', tmp.name], 
                                                  capture_output=True, timeout=10)
                            if result.returncode == 0:
                                logger.info(f"Ecrã capturado temporariamente: {tmp.name}")
                                return True, tmp.name
                
                # Tentar usar slurp para Wayland (interativo)
                if subprocess.run(['which', 'slurp'], capture_output=True).returncode == 0:
                    logger.warning("slurp disponível mas requer interação. Use grim para captura automática.")
            
            # Tentar usar scrot para X11
            if subprocess.run(['which', 'scrot'], capture_output=True).returncode == 0:
                if output_path:
                    result = subprocess.run(['scrot', output_path], 
                                          capture_output=True, timeout=10)
                    if result.returncode == 0:
                        logger.info(f"Ecrã capturado com scrot: {output_path}")
                        return True, output_path
                else:
                    import tempfile
                    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                        result = subprocess.run(['scrot', tmp.name], 
                                              capture_output=True, timeout=10)
                        if result.returncode == 0:
                            logger.info(f"Ecrã capturado temporariamente: {tmp.name}")
                            return True, tmp.name
            
            # Tentar usar gnome-screenshot
            if subprocess.run(['which', 'gnome-screenshot'], capture_output=True).returncode == 0:
                if output_path:
                    result = subprocess.run(['gnome-screenshot', '-f', output_path], 
                                          capture_output=True, timeout=10)
                    if result.returncode == 0:
                        logger.info(f"Ecrã capturado com gnome-screenshot: {output_path}")
                        return True, output_path
                else:
                    import tempfile
                    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                        result = subprocess.run(['gnome-screenshot', '-f', tmp.name], 
                                              capture_output=True, timeout=10)
                        if result.returncode == 0:
                            logger.info(f"Ecrã capturado temporariamente: {tmp.name}")
                            return True, tmp.name
            
            logger.error("Nenhum utilitário de captura de ecrã encontrado")
            return False, "Nenhum utilitário de captura de ecrã encontrado (instale scrot, grim ou gnome-screenshot)"
            
        except subprocess.TimeoutExpired:
            logger.error("Timeout ao capturar ecrã")
            return False, "Timeout ao capturar ecrã"
        except Exception as e:
            logger.error(f"Erro ao capturar ecrã: {e}")
            return False, f"Erro ao capturar ecrã: {e}"
    
    def capture_active_window(self, output_path: str = None) -> Tuple[bool, str]:
        """
        Capturar janela ativa com suporte para Wayland e X11
        
        Args:
            output_path: Caminho para guardar a captura
            
        Returns:
            Tuplo (sucesso, caminho_da_imagem)
        """
        try:
            if self.is_wayland:
                # Tentar usar slurp para Wayland
                if subprocess.run(['which', 'slurp'], capture_output=True).returncode == 0:
                    if output_path:
                        result = subprocess.run(['slurp', '-o', output_path], 
                                              capture_output=True, timeout=10)
                        if result.returncode == 0:
                            logger.info(f"Janela ativa capturada com slurp: {output_path}")
                            return True, output_path
                    else:
                        import tempfile
                        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                            result = subprocess.run(['slurp', '-o', tmp.name], 
                                                  capture_output=True, timeout=10)
                            if result.returncode == 0:
                                logger.info(f"Janela ativa capturada temporariamente: {tmp.name}")
                                return True, tmp.name
            else:
                # Usar scrot para X11
                if subprocess.run(['which', 'scrot'], capture_output=True).returncode == 0:
                    if output_path:
                        result = subprocess.run(['scrot', '-u', output_path], 
                                              capture_output=True, timeout=10)
                        if result.returncode == 0:
                            logger.info(f"Janela ativa capturada com scrot: {output_path}")
                            return True, output_path
                    else:
                        import tempfile
                        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                            result = subprocess.run(['scrot', '-u', tmp.name], 
                                                  capture_output=True, timeout=10)
                            if result.returncode == 0:
                                logger.info(f"Janela ativa capturada temporariamente: {tmp.name}")
                                return True, tmp.name
            
            logger.error("Nenhum utilitário de captura de janela encontrado")
            return False, "Nenhum utilitário de captura de janela encontrado (instale scrot ou slurp)"
            
        except subprocess.TimeoutExpired:
            logger.error("Timeout ao capturar janela ativa")
            return False, "Timeout ao capturar janela ativa"
        except Exception as e:
            logger.error(f"Erro ao capturar janela ativa: {e}")
            return False, f"Erro ao capturar janela ativa: {e}"
    
    def get_active_window_info(self) -> Dict[str, str]:
        """Obter informação sobre a janela ativa"""
        info = {}
        
        try:
            if self.is_wayland:
                # Tentar usar swaymsg para Wayland (Sway)
                if subprocess.run(['which', 'swaymsg'], capture_output=True).returncode == 0:
                    result = subprocess.run(['swaymsg', '-t', 'get_tree'], 
                                          capture_output=True, text=True, timeout=5)
                    if result.returncode == 0:
                        try:
                            import json
                            tree = json.loads(result.stdout)
                            if tree and 'nodes' in tree:
                                # Encontrar janela ativa
                                for node in tree.get('nodes', []):
                                    if node.get('focused', False):
                                        info['title'] = node.get('name', 'Unknown')
                                        info['app_id'] = node.get('app_id', 'Unknown')
                                        break
                        except Exception as e:
                            logger.warning(f"Erro a parsear swaymsg: {e}")
                
                # Tentar usar wl-focus para Wayland
                if subprocess.run(['which', 'wl-focus'], capture_output=True).returncode == 0:
                    result = subprocess.run(['wl-focus', '--get'], 
                                          capture_output=True, text=True, timeout=5)
                    if result.returncode == 0:
                        info['window_id'] = result.stdout.strip()
            else:
                # Usar xdotool para X11
                if subprocess.run(['which', 'xdotool'], capture_output=True).returncode == 0:
                    result = subprocess.run(
                        ['xdotool', 'getactivewindow', 'getwindowname'],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0:
                        info["title"] = result.stdout.strip()
                    
                    result = subprocess.run(
                        ['xdotool', 'getactivewindow', 'getwindowclass'],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0:
                        info["class"] = result.stdout.strip()
                    
                    result = subprocess.run(
                        ['xdotool', 'getactivewindow', 'getwindowgeometry'],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0:
                        info["geometry"] = result.stdout.strip()
                
                # Usar xprop para obter mais info
                if subprocess.run(['which', 'xprop'], capture_output=True).returncode == 0:
                    result = subprocess.run(
                        ['xprop', '-root', '_NET_ACTIVE_WINDOW'],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0:
                        info["window_id"] = result.stdout.strip()
            
        except Exception as e:
            logger.warning(f"Erro a obter info da janela ativa: {e}")
            info["error"] = str(e)
        
        return info
    
    def extract_text_from_image(self, image_path: str, lang: str = "por+eng") -> Tuple[bool, str]:
        """
        Extrair texto de uma imagem usando OCR com fallback
        
        Args:
            image_path: Caminho para a imagem
            lang: Idiomas para OCR (ex: "por+eng")
            
        Returns:
            Tuplo (sucesso, texto_extraído)
        """
        try:
            import pytesseract
            from PIL import Image
            
            # Verificar se ficheiro existe
            if not os.path.exists(image_path):
                return False, f"Ficheiro não encontrado: {image_path}"
            
            # Abrir imagem
            img = Image.open(image_path)
            
            # Extrair texto
            try:
                text = pytesseract.image_to_string(img, lang=lang)
                
                # Se não obtiver texto, tentar com inglês apenas
                if not text.strip():
                    logger.info("Sem texto detetado. A tentar com inglês...")
                    text = pytesseract.image_to_string(img, lang='eng')
                
                if text.strip():
                    logger.info(f"Texto extraído com sucesso ({len(text)} caracteres)")
                    return True, text.strip()
                else:
                    logger.warning("Nenhum texto detetado na imagem")
                    return True, ""
                    
            except Exception as e:
                logger.error(f"Erro no pytesseract: {e}")
                return False, f"Erro no OCR: {e}"
                
        except ImportError as e:
            logger.error(f"pytesseract ou PIL não instalados: {e}")
            return False, "pytesseract ou PIL não instalados (instale com: pip install pytesseract pillow)"
        except Exception as e:
            logger.error(f"Erro ao extrair texto: {e}")
            return False, f"Erro ao extrair texto: {e}"
    
    def get_process_list(self) -> List[Dict[str, str]]:
        """Obter lista de processos em execução"""
        processes = []
        
        try:
            import psutil
            for proc in psutil.process_iter(['pid', 'name', 'username', 'cpu_percent', 'memory_percent']):
                try:
                    info = proc.info
                    processes.append({
                        "pid": str(info['pid']),
                        "name": info['name'] or "Unknown",
                        "user": info['username'] or "Unknown",
                        "cpu": f"{info['cpu_percent']:.1f}%",
                        "memory": f"{info['memory_percent']:.1f}%"
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except ImportError:
            # Fallback sem psutil
            try:
                result = subprocess.run(['ps', 'aux'], capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    for line in result.stdout.split('\n')[1:]:  # Skip header
                        parts = line.split()
                        if len(parts) >= 11:
                            processes.append({
                                "pid": parts[1],
                                "name": parts[10],
                                "user": parts[0],
                                "cpu": parts[2],
                                "memory": parts[3]
                            })
            except Exception as e:
                logger.warning(f"Não foi possível obter lista de processos: {e}")
        
        return sorted(processes, key=lambda x: float(x['cpu'].replace('%', '')), reverse=True)
    
    def get_network_info(self) -> Dict[str, Any]:
        """Obter informação de rede"""
        info = {}
        
        try:
            import psutil
            # Interfaces de rede
            net_io = psutil.net_io_counters(pernic=True)
            info["interfaces"] = {}
            for interface, data in net_io.items():
                info["interfaces"][interface] = {
                    "bytes_sent": f"{data.bytes_sent / 1024:.2f} KB",
                    "bytes_recv": f"{data.bytes_recv / 1024:.2f} KB",
                    "packets_sent": data.packets_sent,
                    "packets_recv": data.packets_recv
                }
            
            # Conexões
            connections = psutil.net_connections(kind='inet')
            info["connections"] = []
            for conn in connections:
                if conn.status == 'ESTABLISHED':
                    info["connections"].append({
                        "local_addr": f"{conn.laddr.ip}:{conn.laddr.port}" if conn.laddr else "N/A",
                        "remote_addr": f"{conn.raddr.ip}:{conn.raddr.port}" if conn.raddr else "N/A",
                        "status": conn.status,
                        "pid": conn.pid
                    })
        except ImportError:
            # Fallback sem psutil
            try:
                result = subprocess.run(['ip', 'addr'], capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    info["interfaces"] = {"output": result.stdout}
                
                result = subprocess.run(['ss', '-tuln'], capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    info["connections"] = result.stdout.split('\n')
            except Exception as e:
                logger.warning(f"Não foi possível obter info de rede: {e}")
        except Exception as e:
            logger.error(f"Erro a obter info de rede: {e}")
            info["error"] = str(e)
        
        return info
    
    def search_files(self, search_term: str, search_path: str = "/", max_results: int = 20) -> List[str]:
        """
        Procurar ficheiros no sistema
        
        Args:
            search_term: Termo a procurar
            search_path: Diretório onde procurar
            max_results: Número máximo de resultados
            
        Returns:
            Lista de caminhos de ficheiros
        """
        results = []
        
        if not search_term or not isinstance(search_term, str):
            return results
        
        try:
            # Usar find para procurar
            cmd = f"find {search_path} -type f -name '*{search_term}*' 2>/dev/null | head -n {max_results}"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0:
                results = [line.strip() for line in result.stdout.split('\n') if line.strip()]
        except Exception as e:
            logger.error(f"Erro ao procurar ficheiros: {e}")
        
        return results
    
    def get_file_info(self, filepath: str) -> Dict[str, str]:
        """Obter informação sobre um ficheiro"""
        info = {}
        
        if not filepath or not isinstance(filepath, str):
            return {"error": "Caminho inválido"}
        
        try:
            file_path = Path(filepath)
            if not file_path.exists():
                return {"error": "Ficheiro não encontrado"}
            
            stat = file_path.stat()
            info["name"] = file_path.name
            info["path"] = str(file_path)
            info["size"] = f"{stat.st_size} bytes"
            info["type"] = "directory" if file_path.is_dir() else "file"
            info["modified"] = time.ctime(stat.st_mtime)
            info["created"] = time.ctime(stat.st_ctime)
            info["accessed"] = time.ctime(stat.st_atime)
            info["permissions"] = oct(stat.st_mode)[-3:]
            info["owner"] = str(stat.st_uid)
            info["group"] = str(stat.st_gid)
            
            # Informação adicional para ficheiros
            if file_path.is_file():
                try:
                    with open(file_path, 'rb') as f:
                        header = f.read(32)
                    info["header"] = header.hex()[:20] + "..."
                except:
                    pass
            
        except Exception as e:
            logger.error(f"Erro a obter info do ficheiro: {e}")
            info["error"] = str(e)
        
        return info
