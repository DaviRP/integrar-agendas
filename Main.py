"""
CLI de integracao: normaliza o relatorio de agendamentos (CSV) e sincroniza com a Clinica nas Nuvens.

Uso:
  python Main.py configurar                          Cadastra/edita/remove empresas (credenciais da API)
  python Main.py status <arquivo.csv>                 Mostra os contadores (pendente/sincronizado/erro)
  python Main.py status <arquivo.csv> --filtro erro    Lista os registros de um status, com o erro da API
  python Main.py sincronizar <arquivo.csv>             Roda a integracao (pede a empresa se houver mais de uma)
  python Main.py reenviar <arquivo.csv>                 Volta os registros com erro para pendente, para reprocessar

Para a interface visual: streamlit run app.py

O estado da sincronizacao fica salvo em "<arquivo.csv>.sync.json" ao lado do CSV, para que
rodar de novo so processe os pendentes/erros (Ctrl+C interrompe a qualquer momento sem perder
o que ja foi sincronizado).
"""
import argparse
import sys

from config import Empresa, carregar_empresas, adicionar_empresa, remover_empresa
from clinica_nuvens import ClinicaNuvensClient, ClinicaNuvensAPIError
from sync_service import (
    STATUS_LABELS, caminho_estado, carregar_registros, salvar_registros,
    contar_status, sincronizar_registro, reenviar_erros,
)


# --------------------------------------------------------------------------- comando: configurar

def cmd_configurar(_args):
    while True:
        empresas = carregar_empresas()
        print("\nEmpresas cadastradas:" if empresas else "\nNenhuma empresa cadastrada ainda.")
        for e in empresas:
            print(f"  - {e.nome}  (cid={e.cid})")
        print("\n[1] Nova empresa  [2] Remover empresa  [3] Testar conexao  [0] Sair")
        opcao = input("> ").strip()

        if opcao == "1":
            nome = input("Nome da empresa: ").strip()
            client_id = input("client_id: ").strip()
            client_secret = input("client_secret: ").strip()
            cid = input("Token da clinica (clinicaNasNuvens-cid): ").strip()
            id_local_agenda = int(input("ID do local de agenda: ").strip())
            id_tipo_consulta = int(input("ID do tipo de consulta: ").strip())
            id_tipo_convenio = int(input("ID do tipo de convenio padrao: ").strip())
            adicionar_empresa(Empresa(
                nome=nome, client_id=client_id, client_secret=client_secret, cid=cid,
                id_local_agenda=id_local_agenda, id_tipo_consulta=id_tipo_consulta,
                id_tipo_convenio=id_tipo_convenio,
            ))
            print(f"Empresa '{nome}' salva.")

        elif opcao == "2":
            nome = input("Nome da empresa a remover: ").strip()
            remover_empresa(nome)
            print("Removida (se existia).")

        elif opcao == "3":
            nome = input("Nome da empresa para testar: ").strip()
            empresa = next((e for e in carregar_empresas() if e.nome == nome), None)
            if not empresa:
                print("Empresa nao encontrada.")
                continue
            client = ClinicaNuvensClient(empresa.client_id, empresa.client_secret, empresa.cid)
            try:
                client.testar_conexao()
                print("Conexao OK.")
            except ClinicaNuvensAPIError as e:
                print(f"Falha na conexao: {e}")

        elif opcao == "0":
            return
        else:
            print("Opcao invalida.")


# --------------------------------------------------------------------------- comando: status

def cmd_status(args):
    registros = carregar_registros(args.csv)
    contagem = contar_status(registros)

    print(f"\nTotal: {len(registros)}")
    for status, label in STATUS_LABELS.items():
        print(f"  {label}: {contagem.get(status, 0)}")

    if args.filtro:
        print(f"\nRegistros com status '{args.filtro}':")
        for r in registros:
            if r["status"] == args.filtro:
                linha = f"  - {r['nome_paciente']} | {r['data_atendimento']} {r['horario']} | {r['procedimento_nome']}"
                if r["status"] == "erro" and r.get("erro"):
                    linha += f" | ERRO: {r['erro']}"
                print(linha)


# --------------------------------------------------------------------------- comando: reenviar

def cmd_reenviar(args):
    registros = carregar_registros(args.csv)
    qtd = reenviar_erros(registros)
    salvar_registros(args.csv, registros)
    print(f"{qtd} registro(s) com erro voltaram para 'pendente'.")


# --------------------------------------------------------------------------- comando: sincronizar

def escolher_empresa() -> Empresa:
    empresas = carregar_empresas()
    if not empresas:
        print("Nenhuma empresa cadastrada. Rode: python Main.py configurar")
        sys.exit(1)
    if len(empresas) == 1:
        return empresas[0]
    print("\nQual empresa deseja usar para sincronizar?")
    for i, e in enumerate(empresas, 1):
        print(f"  [{i}] {e.nome}")
    idx = int(input("> ").strip()) - 1
    return empresas[idx]


def cmd_sincronizar(args):
    registros = carregar_registros(args.csv)
    pendentes = [r for r in registros if r["status"] in ("pendente", "erro")]
    if not pendentes:
        print("Nada para sincronizar (todos já estão sincronizados).")
        return

    empresa = escolher_empresa()
    client = ClinicaNuvensClient(
        empresa.client_id, empresa.client_secret, empresa.cid,
        on_rate_limit=lambda espera: print(f"\n⏳ Limite de requisições atingido (429). Aguardando {espera}s...\n"),
    )

    total = len(pendentes)
    ok = falhas = 0
    i = 0
    print(f"\nSincronizando {total} agendamento(s) com '{empresa.nome}'. Ctrl+C para interromper.\n")

    try:
        for i, registro in enumerate(pendentes, 1):
            sincronizar_registro(client, empresa, registro)
            if registro["status"] == "sincronizado":
                ok += 1
            else:
                falhas += 1
            print(f"[{i}/{total}] {registro['nome_paciente'][:40]:40s} -> {registro['status']}"
                  + (f" ({registro['erro']})" if registro.get("erro") else ""))
            salvar_registros(args.csv, registros)
    except KeyboardInterrupt:
        print(f"\nInterrompido pelo usuário após {i}/{total} processados.")
    finally:
        salvar_registros(args.csv, registros)
        print(f"\nResumo: {ok} sincronizado(s), {falhas} com erro. Estado salvo em {caminho_estado(args.csv)}")


# --------------------------------------------------------------------------- entrypoint

def main():
    parser_cli = argparse.ArgumentParser(description="Integração de Agendamentos com Clínica nas Nuvens")
    sub = parser_cli.add_subparsers(dest="comando", required=True)

    sub.add_parser("configurar", help="Cadastra/edita/remove empresas")

    p_status = sub.add_parser("status", help="Mostra os contadores de sincronização")
    p_status.add_argument("csv")
    p_status.add_argument("--filtro", choices=["pendente", "sincronizado", "erro"], default=None)

    p_sync = sub.add_parser("sincronizar", help="Sincroniza os agendamentos pendentes")
    p_sync.add_argument("csv")

    p_reenviar = sub.add_parser("reenviar", help="Volta os registros com erro para pendente, para reprocessar")
    p_reenviar.add_argument("csv")

    args = parser_cli.parse_args()

    if args.comando == "configurar":
        cmd_configurar(args)
    elif args.comando == "status":
        cmd_status(args)
    elif args.comando == "sincronizar":
        cmd_sincronizar(args)
    elif args.comando == "reenviar":
        cmd_reenviar(args)


if __name__ == "__main__":
    main()
