"""Histórico de conversas persistente — GTK-free e testável headless.

Extrai o writer de history.json de main_window.py. Invariantes que antes
viviam espalhados pela MainWindow (e produziram o duplo save):

- UMA única fila FIFO e UMA thread de escrita: o main loop nunca bloqueia
  numa reescrita completa do ficheiro por mensagem;
- escrita atómica (temp + os.replace + fsync) com teto de 1000 mensagens;
- drenagem garantida no fecho (sentinel na fila).

O MainWindow apenas chama `append(role, content)` e `close()`.
"""

import json
import os
import queue
import threading
import time
from pathlib import Path

# Mesmo teto do contexto em memória (main_window.MAX_HISTORY_MESSAGES).
MAX_HISTORY_MESSAGES = 1000


class HistoryStore:
    """Fila FIFO + writer thread para history.json (um ficheiro partilhado
    pela GUI e pela CLI; escritas são atómicas, leituras best-effort)."""

    def __init__(self, path=None, max_messages: int = MAX_HISTORY_MESSAGES):
        self.path = Path(path) if path else (
            Path.home() / ".config" / "linux_ai_assistant" / "history.json"
        )
        self.max_messages = max_messages
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._closed = False
        self._writer = threading.Thread(
            target=self._loop, name="history-writer", daemon=True
        )
        self._writer.start()

    # ---- API ----

    def load_messages(self):
        """Histórico normalizado [{'role','content'}] para o contexto.

        O on-disk inclui `timestamp`, que não é campo de mensagem: a
        normalização no load evita enviá-lo ao provider.
        """
        try:
            if self.path.exists():
                with open(self.path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, list):
                    return [
                        {"role": m.get("role", "user"), "content": m.get("content", "")}
                        for m in loaded
                        if isinstance(m, dict) and m.get("content")
                    ]
        except Exception as e:
            # logger do módulo de origem é main_window; aqui usamos um próprio
            import logging
            logging.getLogger(__name__).error(f"Error loading history: {e}")
        return []

    def append(self, role: str, content: str, timestamp: float = None):
        """Enfileira uma mensagem para o writer (não bloqueia)."""
        if self._closed:
            return
        self._queue.put({
            "timestamp": timestamp if timestamp is not None else time.time(),
            "role": role,
            "content": content,
        })

    def close(self, timeout: float = 1.0):
        """Drena as escritas pendentes e termina a thread."""
        if self._closed:
            return
        self._closed = True
        try:
            self._queue.put(None)
            self._writer.join(timeout)
        except RuntimeError:
            pass

    # ---- Writer ----

    def _loop(self):
        """Single background writer (FIFO order, atomic)."""
        while True:
            entry = self._queue.get()
            if entry is None:  # sentinel: drain requested at shutdown
                return
            temp_path = None
            try:
                # Drain everything already queued: rewriting the whole file
                # once per message turned a burst of messages into a burst of
                # full rewrites (O(n) I/O per turn, with an fsync each).
                entries = [entry]
                stop = False
                while True:
                    try:
                        item = self._queue.get_nowait()
                    except queue.Empty:
                        break
                    if item is None:  # sentinel arrived mid-drain
                        stop = True
                        break
                    entries.append(item)

                history = []
                if self.path.exists():
                    with open(self.path, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, list):
                        history = loaded
                history.extend(entries)
                history = history[-self.max_messages:]
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temp_path = self.path.with_name(self.path.name + ".tmp")
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(history, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temp_path, self.path)
                if stop:
                    return
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Error saving history: {e}")
                if temp_path is not None:
                    try:
                        os.unlink(temp_path)
                    except OSError:
                        pass
