"""Memória entre execuções das decisões da IA feitas pelos títulos.

Guarda, por tema e por matéria (link normalizado):
- o veredito do filtro de fontes secundárias ("repete" ou "distinta");
- em que cartão a matéria foi juntada pela junção por conteúdo;
- quais matérias já passaram pela junção (para só chamar a IA quando houver
  matéria nova na seção).

Assim cada matéria é avaliada uma vez, e não em toda busca. O arquivo é um
JSON pequeno; no GitHub Actions ele fica no cache entre as execuções. Entradas
com mais de `DIAS` dias são descartadas.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .busca import chave_link

DIAS = 7


class Memoria:
    def __init__(self, caminho: str | Path | None = None, agora: datetime | None = None):
        self.caminho = Path(caminho) if caminho else None
        self.agora = agora or datetime.now(timezone.utc)
        self.dados: dict[str, dict] = {"secundarias": {}, "cartoes": {}, "vistas": {}}
        self.reaproveitadas = 0  # consultas respondidas pela memória (para o log)
        if self.caminho and self.caminho.exists():
            try:
                lido = json.loads(self.caminho.read_text(encoding="utf-8"))
                for k in self.dados:
                    self.dados[k] = dict(lido.get(k) or {})
            except (ValueError, OSError):
                pass  # memória corrompida: começa do zero
        self._limpar()

    @staticmethod
    def _chave(tema: str, link: str) -> str:
        return f"{tema}|{chave_link(link)}"

    def _limpar(self) -> None:
        limite = (self.agora - timedelta(days=DIAS)).isoformat()
        for tabela in self.dados.values():
            for k in [k for k, v in tabela.items() if v.get("t", "") < limite]:
                del tabela[k]

    def _por(self, tabela: str, tema: str, link: str, valor) -> None:
        self.dados[tabela][self._chave(tema, link)] = {"v": valor, "t": self.agora.isoformat()}

    def _ler(self, tabela: str, tema: str, link: str):
        item = self.dados[tabela].get(self._chave(tema, link))
        if item:
            self.reaproveitadas += 1
        return item["v"] if item else None

    # --- fontes secundárias
    def veredito(self, tema: str, link: str) -> str | None:
        return self._ler("secundarias", tema, link)

    def guardar_veredito(self, tema: str, link: str, veredito: str) -> None:
        self._por("secundarias", tema, link, veredito)

    # --- junção por conteúdo
    def cartao(self, tema: str, link: str) -> str | None:
        return self._ler("cartoes", tema, link)

    def guardar_cartao(self, tema: str, links: list[str], cartao: str) -> None:
        for link in links:
            self._por("cartoes", tema, link, cartao)

    def vista(self, tema: str, link: str) -> bool:
        return bool(self._ler("vistas", tema, link))

    def marcar_vistas(self, tema: str, links: list[str]) -> None:
        for link in links:
            self._por("vistas", tema, link, True)

    def salvar(self) -> None:
        if not self.caminho:
            return
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self.caminho.write_text(json.dumps(self.dados, ensure_ascii=False), encoding="utf-8")
