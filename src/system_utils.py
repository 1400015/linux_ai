import os
import re
import subprocess
import platform
import psutil
from typing import Optional, Dict, List, Tuple
from pathlib import Path


class SystemUtils:
    """Utilitários para interagir com o sistema Linux"""
    
    def __init__(self, config_manager):
        self.config = config_manager
        self.allowed_commands = set(config_manager.get("permissions.allowed_commands", []))
        self.allowed_edit_dirs = set(config_manager.get("permissions.allowed_edit_dirs", []))
    
    def get_system_info(self) -> Dict[str, str]:
        """Obter informação do sistema"""
        info = {}
        
        # Informação básica
        info["os"] = platform.system()
        info["release"] = platform.release()
        info["version"] = platform.version()
        info["machine"] = platform.machine()
        info["processor"] = platform.processor()
        
        # Memória
        mem = psutil.virtual_memory()
        info["memory_total"] = f"{mem.total / (1024**3):.2f} GB"
        info["memory_used"] = f"{mem.used / (1024**3):.2f} GB"
        info["memory_percent"] = f"{mem.percent}%"
        
        # CPU
        info["cpu_cores"] = str(psutil.cpu_count(logical=True))
        info["cpu_usage"] = f"{psutil.cpu_percent(interval=1)}%"
        
        # Disco
        disk = psutil.disk_usage('/')
        info["disk_total"] = f"{disk.total / (1024**3):.2f} GB"
        info["disk_used"] = f"{disk.used / (1024**3):.2f} GB"
        info["disk_percent"] = f"{disk.percent}%"
        
        # Uptime
        uptime_seconds = int(time.time() - psutil.boot_time())
        days, remainder = divmod(uptime_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)
        info["uptime"] = f"{days}d {hours}h {minutes}m {seconds}s"
        
        # Utilizador
        info["username"] = os.getlogin()
        info["hostname"] = platform.node()
        
        # Distribuição Linux
        try:
            with open('/etc/os-release', 'r') as f:
                for line in f:
                    if line.startswith('PRETTY_NAME='):
                        info["distro"] = line.split('=')[1].strip().strip('"')
                        break
        except:
            info["distro"] = "Unknown"
        
        return info
    
    def execute_command(self, command: str, timeout: int = 10) -> Tuple[bool, str]:
        """
        Executar um comando no sistema
        
        Args:
            command: Comando a executar
            timeout: Timeout em segundos
            
        Returns:
            Tuplo (sucesso, output)
        """
        # Verificar se o comando é permitido
        cmd_base = command.split()[0] if command.split() else command
        
        if cmd_base not in self.allowed_commands:
            return False, f"Comando não permitido: {cmd_base}"
        
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            
            if result.returncode == 0:
                return True, result.stdout
            else:
                return False, result.stderr or result.stdout
        except subprocess.TimeoutExpired:
            return False, f"Timeout ao executar comando: {command}"
        except Exception as e:
            return False, f"Erro ao executar comando: {e}"
    
    def execute_sudo_command(self, command: str, password: str = None, timeout: int = 10) -> Tuple[bool, str]:
        """
        Executar comando com privilégios sudo
        
        Args:
            command: Comando a executar
            password: Password para sudo (opcional, pode ser pedido interativamente)
            timeout: Timeout em segundos
            
        Returns:
            Tuplo (sucesso, output)
        """
        # Verificar se o comando é permitido
        cmd_base = command.split()[0] if command.split() else command
        
        if cmd_base not in self.allowed_commands:
            return False, f"Comando não permitido: {cmd_base}"
        
        try:
            if password:
                # Usar sudo com password
                cmd = f"echo {password} | sudo -S {command}"
                result = subprocess.run(
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=timeout
                )
            else:
                # Pedir password interativamente
                cmd = f"sudo {command}"
                result = subprocess.run(
                    cmd,
                    shell=True,
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
        # Verificar se o diretório é permitido
        file_path = Path(filepath)
        if not any(str(file_path).startswith(d) for d in self.allowed_edit_dirs):
            return False, f"Acesso negado ao ficheiro: {filepath}"
        
        try:
            if not file_path.exists():
                return False, f"Ficheiro não encontrado: {filepath}"
            
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = []
                for i, line in enumerate(f):
                    if i >= max_lines:
                        lines.append(f"\n... (mais {max_lines} linhas omitidas)")
                        break
                    lines.append(line)
            
            return True, ''.join(lines)
        except PermissionError:
            return False, f"Permissão negada para ler: {filepath}"
        except Exception as e:
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
        # Verificar se o diretório é permitido
        file_path = Path(filepath)
        if not any(str(file_path).startswith(d) for d in self.allowed_edit_dirs):
            return False, f"Acesso negado ao ficheiro: {filepath}"
        
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            mode = 'a' if append else 'w'
            with open(file_path, mode, encoding='utf-8') as f:
                f.write(content)
            
            return True, f"Ficheiro {filepath} {'atualizado' if not append else 'adicionado'} com sucesso"
        except PermissionError:
            return False, f"Permissão negada para escrever: {filepath}"
        except Exception as e:
            return False, f"Erro ao escrever ficheiro: {e}"
    
    def capture_screen(self, output_path: str = None) -> Tuple[bool, str]:
        """
        Capturar ecrã
        
        Args:
            output_path: Caminho para guardar a captura (opcional)
            
        Returns:
            Tuplo (sucesso, caminho_da_imagem)
        """
        try:
            # Tentar usar scrot (mais comum em Linux)
            if subprocess.run(['which', 'scrot'], capture_output=True).returncode == 0:
                if output_path:
                    subprocess.run(['scrot', output_path], check=True)
                    return True, output_path
                else:
                    # Guardar em temp
                    import tempfile
                    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                        subprocess.run(['scrot', tmp.name], check=True)
                        return True, tmp.name
            
            # Tentar usar gnome-screenshot
            elif subprocess.run(['which', 'gnome-screenshot'], capture_output=True).returncode == 0:
                if output_path:
                    subprocess.run(['gnome-screenshot', '-f', output_path], check=True)
                    return True, output_path
                else:
                    import tempfile
                    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                        subprocess.run(['gnome-screenshot', '-f', tmp.name], check=True)
                        return True, tmp.name
            
            else:
                return False, "Nenhum utilitário de captura de ecrã encontrado (instale scrot ou gnome-screenshot)"
                
        except Exception as e:
            return False, f"Erro ao capturar ecrã: {e}"
    
    def capture_active_window(self, output_path: str = None) -> Tuple[bool, str]:
        """
        Capturar janela ativa
        
        Args:
            output_path: Caminho para guardar a captura
            
        Returns:
            Tuplo (sucesso, caminho_da_imagem)
        """
        try:
            # Usar scrot com seleção de janela
            if subprocess.run(['which', 'scrot'], capture_output=True).returncode == 0:
                if output_path:
                    subprocess.run(['scrot', '-u', output_path], check=True)
                    return True, output_path
                else:
                    import tempfile
                    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                        subprocess.run(['scrot', '-u', tmp.name], check=True)
                        return True, tmp.name
            
            return False, "scrot não encontrado"
        except Exception as e:
            return False, f"Erro ao capturar janela ativa: {e}"
    
    def get_active_window_info(self) -> Dict[str, str]:
        """Obter informação sobre a janela ativa"""
        info = {}
        
        try:
            # Usar xdotool para obter info da janela ativa
            if subprocess.run(['which', 'xdotool'], capture_output=True).returncode == 0:
                result = subprocess.run(
                    ['xdotool', 'getactivewindow', 'getwindowname'],
                    capture_output=True,
                    text=True
                )
                info["title"] = result.stdout.strip()
                
                result = subprocess.run(
                    ['xdotool', 'getactivewindow', 'getwindowclass'],
                    capture_output=True,
                    text=True
                )
                info["class"] = result.stdout.strip()
                
                result = subprocess.run(
                    ['xdotool', 'getactivewindow', 'getwindowgeometry'],
                    capture_output=True,
                    text=True
                )
                info["geometry"] = result.stdout.strip()
            
            # Usar xprop para obter mais info
            if subprocess.run(['which', 'xprop'], capture_output=True).returncode == 0:
                result = subprocess.run(
                    ['xprop', '-root', '_NET_ACTIVE_WINDOW'],
                    capture_output=True,
                    text=True
                )
                info["window_id"] = result.stdout.strip()
                
        except Exception as e:
            info["error"] = str(e)
        
        return info
    
    def extract_text_from_image(self, image_path: str) -> Tuple[bool, str]:
        """
        Extrair texto de uma imagem usando OCR
        
        Args:
            image_path: Caminho para a imagem
            
        Returns:
            Tuplo (sucesso, texto_extraído)
        """
        try:
            import pytesseract
            from PIL import Image
            
            # Abrir imagem
            img = Image.open(image_path)
            
            # Extrair texto
            text = pytesseract.image_to_string(img, lang='por+eng')
            
            return True, text.strip()
        except ImportError:
            return False, "pytesseract ou PIL não instalados (instale com: pip install pytesseract pillow)"
        except Exception as e:
            return False, f"Erro ao extrair texto: {e}"
    
    def get_process_list(self) -> List[Dict[str, str]]:
        """Obter lista de processos em execução"""
        processes = []
        
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
        
        return sorted(processes, key=lambda x: float(x['cpu'].replace('%', '')), reverse=True)
    
    def get_network_info(self) -> Dict[str, Any]:
        """Obter informação de rede"""
        info = {}
        
        try:
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
                        "local_addr": f"{conn.laddr.ip}:{conn.laddr.port}",
                        "remote_addr": f"{conn.raddr.ip}:{conn.raddr.port}" if conn.raddr else "N/A",
                        "status": conn.status,
                        "pid": conn.pid
                    })
            
        except Exception as e:
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
        
        try:
            # Usar find para procurar
            cmd = f"find {search_path} -type f -name '*{search_term}*' 2>/dev/null | head -n {max_results}"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            
            if result.returncode == 0:
                results = [line.strip() for line in result.stdout.split('\n') if line.strip()]
        except Exception as e:
            print(f"Erro ao procurar ficheiros: {e}")
        
        return results
    
    def get_file_info(self, filepath: str) -> Dict[str, str]:
        """Obter informação sobre um ficheiro"""
        info = {}
        
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
            info["permissions"] = oct(stat.st_mode)[-3:]
            info["owner"] = str(stat.st_uid)
            
        except Exception as e:
            info["error"] = str(e)
        
        return info
