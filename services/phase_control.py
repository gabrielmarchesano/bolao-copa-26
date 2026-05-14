"""
services/phase_control.py — controle manual de abertura/fechamento de fases.

Permite que o admin force a abertura de uma fase mesmo que seu predecessor
não tenha terminado, ou force o fechamento.

Storage em memória (em produção, seria no BD).
"""
from typing import Set

# Set de fases forçadamente abertas pelo admin
_forced_open_phases: Set[str] = set()
_forced_closed_phases: Set[str] = set()


def force_open_phase(phase_key: str) -> None:
    """Força abertura de uma fase (admin)."""
    _forced_open_phases.add(phase_key)
    _forced_closed_phases.discard(phase_key)


def force_close_phase(phase_key: str) -> None:
    """Força fechamento de uma fase (admin)."""
    _forced_closed_phases.add(phase_key)
    _forced_open_phases.discard(phase_key)


def remove_force_open(phase_key: str) -> None:
    """Remove força de abertura (admin)."""
    _forced_open_phases.discard(phase_key)


def remove_force_close(phase_key: str) -> None:
    """Remove força de fechamento (admin)."""
    _forced_closed_phases.discard(phase_key)


def is_phase_force_open(phase_key: str) -> bool:
    """True se a fase foi forçadamente aberta."""
    return phase_key in _forced_open_phases


def is_phase_force_closed(phase_key: str) -> bool:
    """True se a fase foi forçadamente fechada."""
    return phase_key in _forced_closed_phases


def get_phase_control_status() -> dict:
    """Retorna estado atual do controle manual."""
    return {
        "forced_open": list(_forced_open_phases),
        "forced_closed": list(_forced_closed_phases),
    }
