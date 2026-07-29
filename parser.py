"""
Normaliza o relatorio de agendamentos (CSV hierarquico exportado pelo sistema de origem)
em uma lista plana de registros prontos para integrar com a Clinica nas Nuvens.

Formato de entrada (colunas separadas por ';', encoding ISO-8859-1 / latin1):
  Coluna A (0): linha de cabecalho da EMPRESA (o restante da linha fica vazio, exceto Quantidade)
  Coluna B (1): linha de cabecalho da DATA do atendimento (dd/mm/yyyy)
  Coluna C (2): linha de cabecalho do PROCEDIMENTO ("<codigo>  <nome>")
  Coluna D (3): linha de PACIENTE - Codigo do Usuario na origem (pode ser um CPF de 11 digitos
                ou um codigo interno sequencial - precisa ser normalizado para descobrir qual e)
  Coluna E (4): Nome do paciente
  Coluna F (5): Telefone do Usuario
  Coluna G (6): Telefone Celular do Usuario
  Coluna H (7): Telefone para Contato do Usuario
  Coluna I (8): Horario do agendamento
  Coluna J (9): Quantidade (so preenchida nas linhas de cabecalho)

As linhas de paciente herdam a empresa/data/procedimento do ultimo cabecalho lido acima delas.
"""
import csv
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

CPF_LEN = 11
DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
PROCEDIMENTO_RE = re.compile(r"^(\d+)\s+(.*)$")
HEADER_MARKER = "Código do Usuário"


@dataclass
class Agendamento:
    empresa: str = ""
    data_atendimento: Optional[str] = None       # ISO yyyy-mm-dd
    horario: Optional[str] = None                # HH:MM:SS
    procedimento_codigo: Optional[str] = None
    procedimento_nome: Optional[str] = None
    codigo_origem: str = ""                      # valor bruto da coluna D
    cpf: Optional[str] = None                    # preenchido só quando a coluna D é CPF-shaped
    nome_paciente: str = ""
    telefone1: Optional[str] = None
    telefone2: Optional[str] = None
    status: str = "pendente"                     # pendente | sincronizado | erro
    erro: Optional[str] = None

    def to_dict(self):
        return asdict(self)


def _only_digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _clean(value: str) -> str:
    return (value or "").strip()


def _normalize_cpf(codigo_bruto: str):
    """Se o codigo do usuario tiver o formato de um CPF (11 digitos), retorna (cpf_formatado, None).
    Caso contrario, retorna (None, codigo_bruto) - o codigo fica como referencia externa, sem CPF."""
    digits = _only_digits(codigo_bruto)
    if len(digits) == CPF_LEN:
        cpf = f"{digits[0:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:11]}"
        return cpf, digits
    return None, _clean(codigo_bruto)


def _normalize_phone(value: str):
    digits = _only_digits(value)
    return digits or None


def _normalize_date(value: str):
    m = _clean(value)
    if not DATE_RE.match(m):
        return None
    dt = datetime.strptime(m, "%d/%m/%Y")
    return dt.strftime("%Y-%m-%d")


def parse_csv(path: str, encoding: str = "latin1") -> list[Agendamento]:
    registros: list[Agendamento] = []

    empresa_atual = ""
    data_atual = None
    proc_codigo_atual = None
    proc_nome_atual = None

    with open(path, encoding=encoding, newline="") as f:
        reader = csv.reader(f, delimiter=";")
        for row in reader:
            row = [c for c in row] + [""] * max(0, 10 - len(row))
            col_a, col_b, col_c, col_d, col_e, col_f, col_g, col_h, col_i = (
                _clean(row[0]), _clean(row[1]), _clean(row[2]), _clean(row[3]),
                _clean(row[4]), _clean(row[5]), _clean(row[6]), _clean(row[7]), _clean(row[8]),
            )

            if col_d == HEADER_MARKER:
                continue  # linha de cabecalho repetida (quebra de pagina)

            if col_a:
                empresa_atual = col_a
                continue

            if col_b:
                data_atual = _normalize_date(col_b)
                continue

            if col_c and not col_d:
                match = PROCEDIMENTO_RE.match(col_c)
                if match:
                    proc_codigo_atual, proc_nome_atual = match.group(1), match.group(2).strip()
                else:
                    proc_codigo_atual, proc_nome_atual = None, col_c
                continue

            if col_d and col_e:
                cpf, codigo_origem = _normalize_cpf(col_d)
                telefones = [t for t in (
                    _normalize_phone(col_g),  # celular primeiro (mais confiavel)
                    _normalize_phone(col_f),
                    _normalize_phone(col_h),
                ) if t]
                # remove duplicados mantendo ordem
                telefones = list(dict.fromkeys(telefones))

                registros.append(Agendamento(
                    empresa=empresa_atual,
                    data_atendimento=data_atual,
                    horario=col_i or None,
                    procedimento_codigo=proc_codigo_atual,
                    procedimento_nome=proc_nome_atual,
                    codigo_origem=codigo_origem,
                    cpf=cpf,
                    nome_paciente=col_e,
                    telefone1=telefones[0] if len(telefones) > 0 else None,
                    telefone2=telefones[1] if len(telefones) > 1 else None,
                ))

    return registros


if __name__ == "__main__":
    import sys
    import json

    caminho = sys.argv[1] if len(sys.argv) > 1 else \
        "Relatório de Agendamentos CALE US ECO 4965 - Santos Dumont.csv"
    regs = parse_csv(caminho)
    sem_cpf = [r for r in regs if not r.cpf]
    print(f"Total de agendamentos: {len(regs)}")
    print(f"Sem CPF identificado (precisam de revisão): {len(sem_cpf)}")
    print(json.dumps([r.to_dict() for r in regs[:3]], indent=2, ensure_ascii=False))
