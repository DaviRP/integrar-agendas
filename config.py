"""Configuracao das empresas (credenciais Clinica nas Nuvens) armazenada em empresas.json."""
import json
import os
from dataclasses import dataclass, asdict, field
from typing import Optional

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "empresas.json")


@dataclass
class Empresa:
    nome: str
    client_id: str
    client_secret: str
    cid: str                       # header clinicaNasNuvens-cid
    id_local_agenda: int
    id_tipo_consulta: int
    id_tipo_convenio: int          # convenio padrao usado na associacao paciente-convenio
    status_agendamento: str = "AGENDADO"
    duracao_padrao_minutos: int = 30
    data_nascimento_padrao: str = "2000-01-01"  # yyyy-MM-dd (formato exigido pela API); usada ao criar paciente sem data de nascimento na origem

    def to_dict(self):
        return asdict(self)


def carregar_empresas() -> list[Empresa]:
    if not os.path.exists(CONFIG_PATH):
        return []
    with open(CONFIG_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return [Empresa(**item) for item in data]


def salvar_empresas(empresas: list[Empresa]) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump([e.to_dict() for e in empresas], f, indent=2, ensure_ascii=False)


def adicionar_empresa(empresa: Empresa) -> None:
    empresas = carregar_empresas()
    empresas = [e for e in empresas if e.nome != empresa.nome]
    empresas.append(empresa)
    salvar_empresas(empresas)


def remover_empresa(nome: str) -> None:
    empresas = [e for e in carregar_empresas() if e.nome != nome]
    salvar_empresas(empresas)
