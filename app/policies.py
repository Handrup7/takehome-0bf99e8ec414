"""Resolución de la política aplicable a un hotel.

Decide, a partir de data/documentos.jsonl, cuál es la política aprobada
que rige un hotel y con qué evidencia.

Reglas (enunciado):
- Solo cuentan los documentos ACTIVOS, de ESE hotel, con autoridad
  "approved_policy" y visibles al huésped.  La web y las notas
  (website / untrusted_note) NO otorgan horarios ni precios, aunque
  estén activos.
- Gana la versión activa más alta.
- Si en esa versión hay dos políticas con datos distintos, NO hay
  política ejecutable: se pide revisión humana (estado "conflict").
- Toda resolución válida lleva evidencia: document_id, version y el
  fragmento exacto del documento.

Decisiones documentadas (DECISIONES.md):
- La nota interna (audience "manager") nunca forma parte de la
  resolución de una política para huésped, aunque sea approved_policy.
- La cita (quote) se toma del contenido íntegro del documento: es un
  fragmento exacto, verificable como substring, y evita ambigüedades
  al recortar frases.
"""

import json
from dataclasses import dataclass
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class NoActivePolicy(Exception):
    """El hotel no tiene ninguna política aprobada activa."""


@dataclass(frozen=True)
class PolicyResolution:
    """Resultado de buscar la política aplicable a un hotel.

    status:
      "ok"      -> policy no es None, aplica y se puede usar.
      "conflict"-> hay dos políticas activas de la misma versión máxima
                   con datos distintos: NO hay política ejecutable.
    """

    status: str
    hotel_id: str
    policy: dict | None      # {"normal_checkout", "late_checkout_limit", "fee_eur", ...}
    document: dict | None    # el documento completo elegido (o el conflictante)

    def evidence(self) -> dict:
        """Evidencia en el formato del kit: document_id, version, quote."""
        doc = self.document
        return {
            "document_id": doc["document_id"],
            "version": doc["version"],
            "quote": doc["content"],
        }


def load_documents() -> list[dict]:
    """Carga data/documentos.jsonl (un objeto JSON por línea)."""
    docs = []
    with open(_DATA_DIR / "documentos.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                docs.append(json.loads(line))
    return docs


def resolve_policy(hotel_id: str, documents: list[dict] | None = None) -> PolicyResolution:
    """Busca la política aprobada activa vigente para hotel_id.

    Lanza NoActivePolicy si no existe ninguna.
    Devuelve status "conflict" si la versión máxima está contradicha.
    """
    if documents is None:
        documents = load_documents()

    candidates = [
        d for d in documents
        if d["hotel_id"] == hotel_id
        and d["active"] is True
        and d["authority"] == "approved_policy"
        and d["audience"] == "guest"
        and d.get("policy") is not None
    ]

    if not candidates:
        raise NoActivePolicy(f"No hay política aprobada activa para {hotel_id}")

    max_version = max(d["version"] for d in candidates)
    top = [d for d in candidates if d["version"] == max_version]

    # ¿Coinciden todas las de la versión máxima?
    if len(top) == 1:
        doc = top[0]
        return PolicyResolution("ok", hotel_id, doc["policy"], doc)

    # Dos o más de la misma versión máxima: si discrepan, conflicto.
    first = top[0]["policy"]
    for other in top[1:]:
        if other["policy"] != first:
            return PolicyResolution("conflict", hotel_id, None, top[1])
    # Mismas políticas duplicadas: no es un conflicto real.
    return PolicyResolution("ok", hotel_id, first, top[0])
