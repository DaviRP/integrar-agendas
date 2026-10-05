"""
Interface visual (Streamlit) para normalizar o relatorio de agendamentos e sincronizar
com a Clinica nas Nuvens.

Rodar com: streamlit run app.py
"""
import hashlib
import os

import pandas as pd
import streamlit as st

import re

from config import Empresa, carregar_empresas, adicionar_empresa, remover_empresa, eh_fixa
from clinica_nuvens import ClinicaNuvensClient, ClinicaNuvensAPIError
from sync_service import (
    carregar_registros, salvar_registros, contar_status, sincronizar_registro,
    reenviar_erros, STATUS_LABELS,
)

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

st.set_page_config(page_title="Integração Agendamentos · Clínica nas Nuvens", layout="wide")


def init_state():
    defaults = {
        "csv_path": None,
        "uploaded_hash": None,
        "registros_full": [],
        "status_filtro": None,
        "sync_running": False,
        "sync_targets": [],
        "sync_index": 0,
        "sync_total": 0,
        "sync_empresa": None,
        "sync_log": [],
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def carregar_para_sessao(csv_path: str):
    st.session_state.csv_path = csv_path
    st.session_state.registros_full = carregar_registros(csv_path)
    st.session_state.sync_running = False
    st.session_state.status_filtro = None
    st.session_state.sync_log = []


def persistir():
    salvar_registros(st.session_state.csv_path, st.session_state.registros_full)


def exigir_login():
    """Tela de acesso simples: senha única guardada em st.secrets['APP_PASSWORD'].
    Evita que qualquer pessoa com o link veja CPF/nome/telefone de pacientes."""
    if st.session_state.get("autenticado"):
        return

    st.title("🔒 Integração de Agendamentos")
    senha_configurada = st.secrets.get("APP_PASSWORD")
    if not senha_configurada:
        st.error(
            "APP_PASSWORD não configurada no servidor. "
            "Defina em .streamlit/secrets.toml (local) ou nas 'Secrets' do app (Streamlit Cloud)."
        )
        st.stop()

    with st.form("login"):
        senha = st.text_input("Senha de acesso", type="password")
        entrar = st.form_submit_button("Entrar")
    if entrar:
        if senha == senha_configurada:
            st.session_state.autenticado = True
            st.rerun()
        else:
            st.error("Senha incorreta.")
    st.stop()


init_state()
exigir_login()

st.title("Integração de Agendamentos → Clínica nas Nuvens")

with st.sidebar:
    if st.button("🚪 Sair"):
        st.session_state.autenticado = False
        st.rerun()

tab_agendamentos, tab_empresas = st.tabs(["📅 Agendamentos", "⚙️ Empresas"])

# ============================================================== ABA EMPRESAS
with tab_empresas:
    st.subheader("Empresas cadastradas")
    empresas = carregar_empresas()
    if not empresas:
        st.info("Nenhuma empresa cadastrada ainda.")
    for e in empresas:
        fixa = eh_fixa(e.nome)
        rotulo = "🔒 fixa" if fixa else "⚠️ local (não persiste)"
        with st.expander(f"{e.nome}  ·  cid={e.cid}  ·  {rotulo}"):
            col1, col2 = st.columns(2)
            with col1:
                st.write(f"**ID local agenda:** {e.id_local_agenda}")
                st.write(f"**ID tipo consulta:** {e.id_tipo_consulta}")
                st.write(f"**ID tipo convênio:** {e.id_tipo_convenio}")
            with col2:
                st.write(f"**Duração padrão:** {e.duracao_padrao_minutos} min")
                st.write(f"**Data nasc. padrão:** {e.data_nascimento_padrao}")
                st.write(f"**Status do agendamento:** {e.status_agendamento}")

            btn_col1, btn_col2 = st.columns(2)
            if btn_col1.button("🔌 Testar conexão", key=f"test_{e.nome}"):
                client = ClinicaNuvensClient(e.client_id, e.client_secret, e.cid)
                try:
                    client.testar_conexao()
                    st.success("Conexão OK.")
                except ClinicaNuvensAPIError as err:
                    st.error(f"Falha na conexão: {err}")
            if fixa:
                btn_col2.caption("Empresa fixa: para alterar/remover, edite os Secrets do app.")
            else:
                if btn_col2.button("🗑️ Remover", key=f"del_{e.nome}"):
                    remover_empresa(e.nome)
                    st.rerun()
                st.caption(
                    "Esta empresa está salva só neste servidor e some se ele reiniciar ou em outro "
                    "computador. Para torná-la fixa, cole o bloco abaixo nos Secrets do app "
                    "(Streamlit Cloud → Settings → Secrets, ou .streamlit/secrets.toml local) e salve."
                )
                chave = re.sub(r"[^a-z0-9_]+", "_", e.nome.lower()).strip("_") or "empresa"
                st.code(e.to_toml(chave), language="toml")

    st.divider()
    st.subheader("Nova empresa")
    with st.form("nova_empresa", clear_on_submit=True):
        nome = st.text_input("Nome da empresa")
        col1, col2 = st.columns(2)
        client_id = col1.text_input("client_id")
        client_secret = col2.text_input("client_secret", type="password")
        cid = st.text_input("Token da clínica (clinicaNasNuvens-cid)")

        col3, col4, col5 = st.columns(3)
        id_local_agenda = col3.number_input("ID local da agenda", step=1, min_value=0)
        id_tipo_consulta = col4.number_input("ID tipo de consulta", step=1, min_value=0)
        id_tipo_convenio = col5.number_input("ID tipo de convênio padrão", step=1, min_value=0)

        col6, col7, col8 = st.columns(3)
        duracao = col6.number_input("Duração padrão (min)", value=30, step=5, min_value=5)
        data_nasc_padrao = col7.text_input("Data nasc. padrão (yyyy-MM-dd)", value="2000-01-01")
        status_agendamento = col8.text_input("Status do agendamento", value="AGENDADO")

        submitted = st.form_submit_button("Salvar empresa")
        if submitted:
            if not nome or not client_id or not client_secret or not cid:
                st.error("Preencha nome, client_id, client_secret e o token da clínica.")
            elif eh_fixa(nome):
                st.error(f"'{nome}' já é uma empresa fixa (Secrets). Use outro nome ou edite os Secrets.")
            else:
                adicionar_empresa(Empresa(
                    nome=nome, client_id=client_id, client_secret=client_secret, cid=cid,
                    id_local_agenda=int(id_local_agenda), id_tipo_consulta=int(id_tipo_consulta),
                    id_tipo_convenio=int(id_tipo_convenio), duracao_padrao_minutos=int(duracao),
                    data_nascimento_padrao=data_nasc_padrao, status_agendamento=status_agendamento,
                ))
                st.success(f"Empresa '{nome}' salva.")
                st.rerun()

# ========================================================= ABA AGENDAMENTOS
with tab_agendamentos:
    uploaded = st.file_uploader("Envie o relatório de agendamentos (CSV)", type=["csv"])
    if uploaded is not None:
        conteudo = uploaded.getbuffer()
        hash_atual = hashlib.md5(conteudo).hexdigest()
        # o widget mantém o mesmo arquivo "selecionado" em toda a sessão (inclusive durante
        # os reruns da sincronização) - só reprocessa quando o conteúdo realmente muda,
        # o que também cobre reenviar um arquivo com o mesmo nome mas dados atualizados.
        if hash_atual != st.session_state.uploaded_hash:
            dest_path = os.path.join(UPLOAD_DIR, uploaded.name)
            with open(dest_path, "wb") as f:
                f.write(conteudo)
            st.session_state.uploaded_hash = hash_atual
            carregar_para_sessao(dest_path)

    csv_path = st.session_state.csv_path
    if not csv_path:
        st.info("Envie um CSV para começar.")
    else:
        registros = st.session_state.registros_full
        contagem = contar_status(registros)

        st.caption(f"Arquivo: {os.path.basename(csv_path)}")

        c0, c1, c2, c3 = st.columns(4)
        c0.metric("Total", len(registros))
        if c1.button(f"🟡 Pendentes\n\n{contagem['pendente']}", use_container_width=True):
            st.session_state.status_filtro = "pendente"
        if c2.button(f"🟢 Sincronizados\n\n{contagem['sincronizado']}", use_container_width=True):
            st.session_state.status_filtro = "sincronizado"
        if c3.button(f"🔴 Com erro\n\n{contagem['erro']}", use_container_width=True):
            st.session_state.status_filtro = "erro"

        colunas_exibicao = ["nome_paciente", "cpf", "codigo_origem", "data_atendimento",
                             "horario", "procedimento_nome", "telefone1", "telefone2", "status"]

        if st.session_state.status_filtro:
            filtro = st.session_state.status_filtro
            filtrados = [r for r in registros if r["status"] == filtro]
            st.markdown(f"**{STATUS_LABELS[filtro]}: {len(filtrados)} registro(s)**")
            cols = colunas_exibicao + (["erro"] if filtro == "erro" else [])
            df = pd.DataFrame(filtrados, columns=cols) if filtrados else pd.DataFrame(columns=cols)
            st.dataframe(df, use_container_width=True, hide_index=True)

            col_fechar, col_reenviar = st.columns(2)
            if col_fechar.button("Fechar lista"):
                st.session_state.status_filtro = None
                st.rerun()
            if filtro == "erro" and filtrados:
                if col_reenviar.button("🔁 Reenviar erros para pendente", use_container_width=True):
                    qtd = reenviar_erros(registros)
                    persistir()
                    st.session_state.status_filtro = None
                    st.success(f"{qtd} registro(s) voltaram para 'pendente'.")
                    st.rerun()
        else:
            st.markdown("**Todos os registros normalizados**")
            df_all = pd.DataFrame(registros, columns=colunas_exibicao)
            st.dataframe(df_all, use_container_width=True, hide_index=True, height=320)

        st.divider()
        st.subheader("Sincronizar com a Clínica nas Nuvens")

        empresas = carregar_empresas()
        if not empresas:
            st.warning("Cadastre uma empresa na aba 'Empresas' antes de sincronizar.")
        else:
            nomes_empresas = [e.nome for e in empresas]
            empresa_nome = st.selectbox("Empresa", nomes_empresas, disabled=st.session_state.sync_running)

            col_a, col_b = st.columns(2)
            iniciar = col_a.button("▶️ Sincronizar", disabled=st.session_state.sync_running, use_container_width=True)
            interromper = col_b.button("⏹️ Interromper", disabled=not st.session_state.sync_running, use_container_width=True)

            if interromper:
                st.session_state.sync_running = False
                st.session_state.sync_log.append("⏹️ Interrompido pelo usuário.")

            if iniciar:
                alvos = [r for r in registros if r["status"] in ("pendente", "erro")]
                if not alvos:
                    st.info("Nada pendente para sincronizar.")
                else:
                    st.session_state.sync_targets = alvos
                    st.session_state.sync_total = len(alvos)
                    st.session_state.sync_index = 0
                    st.session_state.sync_empresa = empresa_nome
                    st.session_state.sync_log = []
                    st.session_state.sync_running = True
                    st.rerun()

            if st.session_state.sync_running:
                idx = st.session_state.sync_index
                total = st.session_state.sync_total
                st.progress(idx / total if total else 0, text=f"{idx}/{total} processados")

                if idx < total:
                    empresa = next(e for e in empresas if e.nome == st.session_state.sync_empresa)
                    client = ClinicaNuvensClient(
                        empresa.client_id, empresa.client_secret, empresa.cid,
                        on_rate_limit=lambda espera: st.session_state.sync_log.append(
                            f"⏳ Limite de requisições atingido (429). Aguardando {espera}s antes de continuar..."
                        ),
                    )
                    registro = st.session_state.sync_targets[idx]

                    sincronizar_registro(client, empresa, registro)
                    persistir()

                    linha = f"[{idx + 1}/{total}] {registro['nome_paciente']} → {registro['status']}"
                    if registro.get("erro"):
                        linha += f"  ·  {registro['erro']}"
                    st.session_state.sync_log.append(linha)
                    st.session_state.sync_index += 1
                    st.rerun()
                else:
                    st.session_state.sync_running = False
                    st.success("Sincronização concluída.")
                    st.rerun()

            if st.session_state.sync_log:
                ok = sum(1 for l in st.session_state.sync_log if "sincronizado" in l)
                erro = sum(1 for l in st.session_state.sync_log if "→ erro" in l)
                if not st.session_state.sync_running:
                    st.caption(f"Último lote: {ok} sincronizado(s), {erro} com erro.")
                with st.expander("Log da sincronização", expanded=st.session_state.sync_running):
                    st.code("\n".join(st.session_state.sync_log[-300:]), language=None)
