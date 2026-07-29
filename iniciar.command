#!/bin/bash
# Duplo-clique neste arquivo para abrir a interface de integração de agendamentos.
cd "$(dirname "$0")"
export PATH="$HOME/Library/Python/3.9/bin:$PATH"
streamlit run app.py
