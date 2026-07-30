"""
Normaliza o relatorio de agendamentos (CSV hierarquico exportado pelo sistema de origem)
em uma lista plana de registros prontos para integrar com a Clinica nas Nuvens.

O relatorio tem uma estrutura hierarquica (colunas separadas por ';', encoding latin1):
  EMPRESA (coluna A)
    DATA do atendimento (coluna B, dd/mm/yyyy)
      PROCEDIMENTO (coluna C, "<codigo>  <nome>")
        [opcional] resumo do paciente: "<codigo>  <nome>  <CPF>" numa unica celula,
                    presente so em algumas variantes do relatorio - quando existe, e a
                    unica fonte confiavel do CPF, que nao aparece em mais lugar nenhum.
        detalhe do paciente: Codigo do Usuario, Nome, 3 telefones e Horario

O numero de colunas vazias antes de cada nivel VARIA entre variantes do relatorio (relatorios
com o nivel de "resumo do paciente" tem uma coluna a mais a partir do proprio paciente em diante).
Por isso os campos da linha de detalhe sao lidos por posicao relativa ao FIM da linha (sempre
"...codigo, nome, telefone_usuario, telefone_celular, telefone_contato, horario, quantidade"),
em vez de por indice fixo - o que faz o parser funcionar em qualquer variante observada.
"""
import csv
import re
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional

# Incrementar sempre que a lógica de parsing mudar, para invalidar automaticamente
# qualquer cache de sincronização (*.sync.json) gerado por uma versão anterior/com bugs.
PARSER_VERSION = 2

CPF_LEN = 11
DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
TIME_RE = re.compile(r"^\d{2}:\d{2}:\d{2}$")
PROCEDIMENTO_RE = re.compile(r"^(\d+)\s+(.*)$")
RESUMO_PACIENTE_RE = re.compile(r"^(\S+)\s+(.+?)\s+(\d{3}\.\d{3}\.\d{3}-\d{2})$")
HEADER_MARKER = "Código do Usuário"


@dataclass
class Agendamento:
    empresa: str = ""
    data_atendimento: Optional[str] = None       # ISO yyyy-mm-dd
    horario: Optional[str] = None                # HH:MM:SS
    procedimento_codigo: Optional[str] = None
    procedimento_nome: Optional[str] = None
    codigo_origem: str = ""                      # codigo do usuario na origem
    cpf: Optional[str] = None                    # vindo do codigo (se for CPF-shaped) ou da linha de resumo
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


def _cpf_do_codigo(codigo_bruto: str):
    """Se o codigo do usuario tiver o formato de um CPF (11 digitos), retorna (cpf_formatado, digitos).
    Caso contrario, retorna (None, codigo_limpo) - o codigo fica como referencia externa, sem CPF."""
    digits = _only_digits(codigo_bruto)
    if len(digits) == CPF_LEN:
        return f"{digits[0:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:11]}", digits
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
    cpf_por_codigo: dict[str, str] = {}  # preenchido pelas linhas de "resumo do paciente", quando existem

    with open(path, encoding=encoding, newline="") as f:
        reader = csv.reader(f, delimiter=";")
        for row in reader:
            row = [_clean(c) for c in row]
            if not any(row) or HEADER_MARKER in row:
                continue

            col_a = row[0] if len(row) > 0 else ""
            col_b = row[1] if len(row) > 1 else ""
            col_c = row[2] if len(row) > 2 else ""

            if col_a:
                empresa_atual = col_a
                continue

            if col_b and DATE_RE.match(col_b):
                data_atual = _normalize_date(col_b)
                continue

            if col_c:
                match = PROCEDIMENTO_RE.match(col_c)
                if match:
                    proc_codigo_atual, proc_nome_atual = match.group(1), match.group(2).strip()
                else:
                    proc_codigo_atual, proc_nome_atual = None, col_c
                continue

            # linha de detalhe do paciente: identificada pelo horario (penultima celula) em HH:MM:SS
            if len(row) >= 7 and TIME_RE.match(row[-2]):
                codigo_bruto, nome = row[-7], row[-6]
                tel_usuario, tel_celular, tel_contato = row[-5], row[-4], row[-3]
                horario = row[-2]

                cpf_heuristico, codigo_origem = _cpf_do_codigo(codigo_bruto)
                cpf = cpf_por_codigo.get(codigo_origem) or cpf_heuristico

                telefones = [t for t in (
                    _normalize_phone(tel_celular),  # celular primeiro (mais confiavel)
                    _normalize_phone(tel_usuario),
                    _normalize_phone(tel_contato),
                ) if t]
                telefones = list(dict.fromkeys(telefones))  # remove duplicados mantendo ordem

                registros.append(Agendamento(
                    empresa=empresa_atual,
                    data_atendimento=data_atual,
                    horario=horario or None,
                    procedimento_codigo=proc_codigo_atual,
                    procedimento_nome=proc_nome_atual,
                    codigo_origem=codigo_origem,
                    cpf=cpf,
                    nome_paciente=nome,
                    telefone1=telefones[0] if len(telefones) > 0 else None,
                    telefone2=telefones[1] if len(telefones) > 1 else None,
                ))
                continue

            # linha de resumo do paciente (algumas variantes trazem "codigo nome CPF" numa unica celula)
            for cell in row:
                m = RESUMO_PACIENTE_RE.match(cell)
                if m:
                    codigo_bruto, _nome, cpf_str = m.groups()
                    codigo_origem = _only_digits(codigo_bruto) or _clean(codigo_bruto)
                    cpf_por_codigo[codigo_origem] = cpf_str
                    break

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
