"""Verificación rápida de policies.py (no es parte de la app, solo sanity check).

Uso (dentro del venv):
    python scripts/policies_probe.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.policies import (  # noqa: E402
    NoActivePolicy,
    load_documents,
    resolve_policy,
)


def show(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


docs = load_documents()

# 1) hotel-alba: debe ganar approved_policy v2 (14:00, 25 EUR), no la web v7
#    ni la nota, y no la v1 inactiva.
show("1) hotel-alba: política vigente")
r = resolve_policy("hotel-alba", docs)
print(f"status={r.status}")
if r.policy:
    print(f"policy={r.policy}")
    print(f"evidence={r.evidence()}")

# 2) hotel-alba-b (Brisa): 12:00, 15 EUR.
show("2) hotel-alba-b: política vigente")
r = resolve_policy("hotel-alba-b", docs)
print(f"status={r.status}")
if r.policy:
    print(f"policy={r.policy}")

# 3) P04 variante: añadir otra approved_policy activa de hotel-alba, versión 2,
#    fee 20 (discordante). Debe dar "conflict" y no hay política ejecutable.
show("3) hotel-alba con 2 políticas v2 discordantes (variante P04)")
conflict_docs = docs + [
    {
        "document_id": "coral-alba-policy-alt",
        "org_id": "org-coral",
        "hotel_id": "hotel-alba",
        "version": 2,
        "authority": "approved_policy",
        "active": True,
        "audience": "guest",
        "content": "Política alternativa: salida tardía hasta 14:00 por 20 EUR.",
        "policy": {
            "normal_checkout": "11:00",
            "late_checkout_limit": "14:00",
            "fee_eur": 20,
            "requires_manager_approval": True,
        },
    }
]
r = resolve_policy("hotel-alba", conflict_docs)
print(f"status={r.status}  (esperado: conflict)")
print(f"policy={r.policy}  (esperado: None, no ejecutable)")

# 4) Hotel sin política: NoActivePolicy.
show("4) hotel-inexistente: sin política")
try:
    resolve_policy("hotel-no-existe", docs)
    print("ERROR: debería haber lanzado NoActivePolicy")
except NoActivePolicy as e:
    print(f"NoActivePolicy -> {e}")
