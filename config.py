"""Configuracao das empresas (credenciais Clinica nas Nuvens).

Duas origens:
- Fixas: secao [empresas] dos Secrets do Streamlit (.streamlit/secrets.toml local ou "Secrets"
  no Streamlit Cloud). Persistem entre reinicios/computadores e nao podem ser removidas pela tela.
- Locais: empresas.json, cadastradas pela tela/CLI. Somem se o servidor reiniciar ou em outro
  computador - servem para testar antes de passar a empresa para os Secrets.
"""
import json
import os
from dataclasses import dataclass, asdict, fields

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

    def to_toml(self, chave: str) -> str:
        """Bloco pronto para colar nos Secrets e tornar a empresa fixa."""
        linhas = [f"[empresas.{chave}]"]
        for k, v in self.to_dict().items():
            linhas.append(f"{k} = {json.dumps(v, ensure_ascii=False)}")
        return "\n".join(linhas)


def _empresa_de_dict(data: dict) -> Empresa:
    campos = {f.name for f in fields(Empresa)}
    return Empresa(**{k: v for k, v in dict(data).items() if k in campos})


def carregar_empresas_fixas() -> list[Empresa]:
    try:
        import streamlit as st
        secao = st.secrets.get("empresas")
    except Exception:  # sem secrets.toml (ex.: CLI fora da pasta do projeto)
        return []
    if not secao:
        return []
    itens = secao.values() if hasattr(secao, "values") else secao
    return [_empresa_de_dict(item) for item in itens]


def carregar_empresas_locais() -> list[Empresa]:
    if not os.path.exists(CONFIG_PATH):
        return []
    with open(CONFIG_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return [_empresa_de_dict(item) for item in data]


def carregar_empresas() -> list[Empresa]:
    fixas = carregar_empresas_fixas()
    nomes_fixos = {e.nome for e in fixas}
    return fixas + [e for e in carregar_empresas_locais() if e.nome not in nomes_fixos]


def eh_fixa(nome: str) -> bool:
    return any(e.nome == nome for e in carregar_empresas_fixas())


def salvar_empresas(empresas: list[Empresa]) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump([e.to_dict() for e in empresas], f, indent=2, ensure_ascii=False)


def adicionar_empresa(empresa: Empresa) -> None:
    empresas = carregar_empresas_locais()
    empresas = [e for e in empresas if e.nome != empresa.nome]
    empresas.append(empresa)
    salvar_empresas(empresas)


def atualizar_empresa(nome_antigo: str, empresa: Empresa) -> None:
    """Substitui a empresa local mantendo a posicao na lista (permite renomear)."""
    empresas = [empresa if e.nome == nome_antigo else e for e in carregar_empresas_locais()]
    salvar_empresas(empresas)


def remover_empresa(nome: str) -> None:
    empresas = [e for e in carregar_empresas_locais() if e.nome != nome]
    salvar_empresas(empresas)
