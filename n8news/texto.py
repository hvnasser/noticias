"""Funções de texto compartilhadas: normalização e palavras-chave."""

from __future__ import annotations

import re
import unicodedata


def normalizar(texto: str) -> str:
    """Minúsculas, sem acentos/apóstrofos/pontuação, com espaço nas pontas."""
    texto = unicodedata.normalize("NFD", texto.lower())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = re.sub(r"['’`´]s\b", " s", texto)  # possessivo inglês: "BoE's" -> "boe s"
    texto = re.sub(r"['’`´]", "", texto)
    texto = re.sub(r"[^a-z0-9*]+", " ", texto)
    return f" {texto.strip()} "


def compilar(palavras: list[str]) -> list[tuple[str, re.Pattern]]:
    """Palavra inteira; `*` no fim aceita qualquer final."""
    padroes = []
    for p in palavras:
        norm = normalizar(str(p)).strip()
        if not norm:
            continue
        regex = r"\b" + re.escape(norm).replace(r"\*", r"\w*") + r"\b"
        padroes.append((str(p), re.compile(regex)))
    return padroes


def primeira(padroes, texto_norm: str) -> str | None:
    for original, regex in padroes:
        if regex.search(texto_norm):
            return original
    return None
