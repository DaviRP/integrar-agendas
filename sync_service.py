"""Logica de sincronizacao compartilhada entre o CLI (Main.py) e a interface web (app.py)."""
import json
import os
from datetime import datetime, timedelta

from parser import parse_csv
from config import Empresa
from clinica_nuvens import ClinicaNuvensClient, ClinicaNuvensAPIError

STATUS_LABELS = {"pendente": "Pendentes", "sincronizado": "Sincronizados", "erro": "Com erro"}


def caminho_estado(csv_path: str) -> str:
    return f"{csv_path}.sync.json"


def carregar_registros(csv_path: str) -> list[dict]:
    estado_path = caminho_estado(csv_path)
    if os.path.exists(estado_path):
        with open(estado_path, encoding="utf-8") as f:
            return json.load(f)
    registros = [r.to_dict() for r in parse_csv(csv_path)]
    salvar_registros(csv_path, registros)
    return registros


def salvar_registros(csv_path: str, registros: list[dict]) -> None:
    with open(caminho_estado(csv_path), "w", encoding="utf-8") as f:
        json.dump(registros, f, indent=2, ensure_ascii=False)


def contar_status(registros: list[dict]) -> dict:
    contagem = {"pendente": 0, "sincronizado": 0, "erro": 0}
    for r in registros:
        contagem[r["status"]] = contagem.get(r["status"], 0) + 1
    return contagem


def reenviar_erros(registros: list[dict]) -> int:
    """Volta os registros com status 'erro' para 'pendente', para serem reprocessados. Retorna quantos foram reenviados."""
    reenviados = 0
    for r in registros:
        if r["status"] == "erro":
            r["status"] = "pendente"
            r["erro"] = None
            reenviados += 1
    return reenviados


def _hora_inicio_fim(horario: str, duracao_minutos: int) -> tuple[str, str]:
    """A API exige HH:mm:ss. `horario` já vem nesse formato do relatório de origem."""
    inicio = datetime.strptime(horario[:8], "%H:%M:%S")
    fim = inicio + timedelta(minutes=duracao_minutos)
    return inicio.strftime("%H:%M:%S"), fim.strftime("%H:%M:%S")


def sincronizar_registro(client: ClinicaNuvensClient, empresa: Empresa, registro: dict) -> None:
    if not registro.get("cpf") and not registro.get("codigo_origem"):
        registro["status"] = "erro"
        registro["erro"] = "Nenhum identificador de paciente disponível (sem CPF e sem código de origem)."
        return
    if not registro.get("data_atendimento") or not registro.get("horario"):
        registro["status"] = "erro"
        registro["erro"] = "Data ou horário do atendimento ausente."
        return

    try:
        id_paciente, _criado = client.buscar_ou_criar_paciente(
            nome=registro["nome_paciente"],
            cpf=registro.get("cpf"),
            codigo_origem=registro.get("codigo_origem"),
            telefone=registro.get("telefone1"),
            data_nascimento_padrao=empresa.data_nascimento_padrao,
        )
        id_paciente_convenio, _associado = client.obter_id_paciente_convenio(
            id_paciente, empresa.id_tipo_convenio,
        )
        hora_inicio, hora_fim = _hora_inicio_fim(registro["horario"], empresa.duracao_padrao_minutos)
        client.criar_agendamento(
            data=registro["data_atendimento"],
            hora_inicio=hora_inicio,
            hora_fim=hora_fim,
            id_paciente=id_paciente,
            id_paciente_convenio=id_paciente_convenio,
            id_local_agenda=empresa.id_local_agenda,
            id_tipo_consulta=empresa.id_tipo_consulta,
            status=empresa.status_agendamento,
            observacoes=registro.get("procedimento_nome"),
            telefone_celular_paciente=registro.get("telefone1"),
        )
        registro["status"] = "sincronizado"
        registro["erro"] = None
    except ClinicaNuvensAPIError as e:
        registro["status"] = "erro"
        registro["erro"] = str(e)
    except Exception as e:
        registro["status"] = "erro"
        registro["erro"] = f"Erro inesperado: {e}"
