"""Client da API da Clinica nas Nuvens.

Fluxo de integracao (seguindo o passo a passo validado com o time):
  1. Buscar paciente por CPF (GET /paciente/lista?cpfCnpj=...), quando disponivel.
     Quando o registro de origem nao tem CPF, busca por numeroIdentificacao=codigo_origem
     (GET /paciente/lista?numeroIdentificacao=...) em vez disso.
     Se existir por qualquer uma das duas buscas, reusa o ID. Se nao existir, cria
     (POST /paciente/novo) com numeroIdentificacao=codigo_origem sempre preenchido.
  2. Verificar se o paciente ja tem o convenio padrao associado
     (GET /convenio-paciente/lista?idPaciente=...&somenteAtivos=true).
     Se nao tiver, associa (POST /convenio-paciente/associar) para obter o ID da associacao
     (idPacienteConvenio), que e o que a agenda espera - nao o ID do convenio em si.
  3. Criar o agendamento (POST /agenda/novo) sem idOrigemPaciente/idPessoaExecutor.

Formato de data/hora (confirmado pela API): data = yyyy-MM-dd, hora = HH:mm:ss.

Limite de requisicoes: 120/token/minuto. Ao receber 429, o client espera (Retry-After do
header, ou 60s por padrao) e repete a mesma chamada automaticamente, em vez de falhar.
"""
import time

import requests

BASE_URL = "https://api.clinicanasnuvens.com.br"
RATE_LIMIT_ESPERA_PADRAO_SEGUNDOS = 60


class ClinicaNuvensAPIError(Exception):
    def __init__(self, status_code, mensagem, payload=None):
        self.status_code = status_code
        self.mensagem = mensagem
        self.payload = payload
        super().__init__(f"[{status_code}] {mensagem}")


class ClinicaNuvensClient:
    def __init__(self, client_id: str, client_secret: str, cid: str, base_url: str = BASE_URL,
                 on_rate_limit=None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.cid = cid
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.on_rate_limit = on_rate_limit  # callback(segundos_espera) chamado antes de cada pausa por 429

    def _headers(self):
        return {
            "accept": "application/json",
            "content-type": "application/json",
            "clinicaNasNuvens-cid": self.cid,
        }

    def _request(self, method, path, params=None, json_body=None):
        url = f"{self.base_url}{path}"
        while True:
            resp = self.session.request(
                method, url,
                params=params, json=json_body,
                headers=self._headers(),
                auth=(self.client_id, self.client_secret),
                timeout=30,
            )
            if resp.status_code == 429:
                espera = _segundos_retry_after(resp) or RATE_LIMIT_ESPERA_PADRAO_SEGUNDOS
                if self.on_rate_limit:
                    self.on_rate_limit(espera)
                time.sleep(espera)
                continue
            if resp.status_code >= 400:
                raise ClinicaNuvensAPIError(resp.status_code, _extrair_mensagem_erro(resp))
            if not resp.content:
                return None
            return resp.json()

    def testar_conexao(self):
        self._request("GET", "/paciente/lista", params={"registrosPorPagina": 1, "pagina": 1})
        return True

    # ---------------------------------------------------------------- paciente
    def buscar_paciente_por_cpf(self, cpf: str):
        data = self._request("GET", "/paciente/lista", params={"cpfCnpj": cpf})
        lista = (data or {}).get("lista") or []
        return lista[0] if lista else None

    def buscar_paciente_por_numero_identificacao(self, numero_identificacao: str):
        data = self._request("GET", "/paciente/lista", params={"numeroIdentificacao": numero_identificacao})
        lista = (data or {}).get("lista") or []
        return lista[0] if lista else None

    def criar_paciente(self, nome: str, cpf: str = None, numero_identificacao: str = None,
                        telefone: str = None, data_nascimento: str = "2000-01-01"):
        body = {
            "nome": nome,
            "dataNascimento": data_nascimento,
        }
        if cpf:
            body["cpfcnpj"] = cpf
        if numero_identificacao:
            body["numeroIdentificacao"] = numero_identificacao
        if telefone:
            body["contato"] = {"telefoneCelular": telefone}
        return self._request("POST", "/paciente/novo", json_body=body)

    def buscar_ou_criar_paciente(self, nome: str, cpf: str, codigo_origem: str,
                                  telefone: str, data_nascimento_padrao: str):
        paciente = self.buscar_paciente_por_cpf(cpf) if cpf else None
        if not paciente and codigo_origem:
            paciente = self.buscar_paciente_por_numero_identificacao(codigo_origem)
        if paciente:
            return paciente["id"], False
        paciente = self.criar_paciente(nome, cpf, codigo_origem, telefone, data_nascimento_padrao)
        return paciente["id"], True

    # ---------------------------------------------------------- convenio-paciente
    def listar_convenios_paciente(self, id_paciente: int, somente_ativos: bool = True):
        data = self._request("GET", "/convenio-paciente/lista", params={
            "idPaciente": id_paciente, "somenteAtivos": somente_ativos,
        })
        return (data or {}).get("lista") or []

    def associar_convenio(self, id_paciente: int, id_tipo_convenio: int):
        return self._request("POST", "/convenio-paciente/associar", json_body={
            "idPaciente": id_paciente,
            "idTipoConvenio": id_tipo_convenio,
        })

    def obter_id_paciente_convenio(self, id_paciente: int, id_tipo_convenio: int):
        """Retorna o ID da associacao paciente-convenio (idPacienteConvenio), criando-a se preciso."""
        associacoes = self.listar_convenios_paciente(id_paciente)
        for assoc in associacoes:
            if assoc.get("idTipoConvenio") == id_tipo_convenio:
                return assoc["id"], False
        nova = self.associar_convenio(id_paciente, id_tipo_convenio)
        return nova["id"], True

    # ------------------------------------------------------------------- agenda
    def criar_agendamento(self, *, data: str, hora_inicio: str, hora_fim: str,
                           id_paciente: int, id_paciente_convenio: int,
                           id_local_agenda: int, id_tipo_consulta: int,
                           status: str = "AGENDADO", observacoes: str = None,
                           telefone_celular_paciente: str = None):
        body = {
            "data": data,
            "horaInicio": hora_inicio,
            "horaFim": hora_fim,
            "idPaciente": id_paciente,
            "idPacienteConvenio": id_paciente_convenio,
            "idLocalAgenda": id_local_agenda,
            "idTipoConsulta": id_tipo_consulta,
            "status": status,
        }
        if observacoes:
            body["observacoes"] = observacoes[:500]
        if telefone_celular_paciente:
            body["telefoneCelularPaciente"] = telefone_celular_paciente
        return self._request("POST", "/agenda/novo", json_body=body)


def _segundos_retry_after(resp: requests.Response):
    valor = resp.headers.get("Retry-After")
    if not valor:
        return None
    try:
        return max(1, int(float(valor)))
    except ValueError:
        return None


def _extrair_mensagem_erro(resp: requests.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return (resp.text or resp.reason or "erro desconhecido").strip()[:500]

    if isinstance(body, dict):
        for chave in ("message", "mensagem", "erro", "error", "detail"):
            if body.get(chave):
                return str(body[chave])
        if isinstance(body.get("errors"), list) and body["errors"]:
            return "; ".join(str(e) for e in body["errors"])
    return str(body)[:500]
