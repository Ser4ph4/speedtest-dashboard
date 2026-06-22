#!/bin/bash

# ── CONFIGURAÇÕES ──────────────────────────────────────────────────────────
HOST_NAME="💻Note"                  # Ex: "Server-Pi" ou "Desktop-Vlan"
API_URL="http://127.0.0.1:5300/api/push" # IP de onde a stack Flask está rodando
API_KEY="X1iOxOCR2EvqgMThFcyGI01TLoAuNqTe"    # Deve ser idêntica à definida no docker-compose

# ── EXECUÇÃO ───────────────────────────────────────────────────────────────

# Executa o speedtest oficial aceitando os termos e cuspindo em JSON limpo
echo "Iniciando teste de velocidade para $HOST_NAME..."
RAW_OUTPUT=$(speedtest --accept-license --accept-gdpr -f json 2>/dev/null)

if [ -z "$RAW_OUTPUT" ]; then
    echo "Erro: O Speedtest falhou ou retornou um output vazio."
    exit 1
fi

# Monta o payload JSON envelopando o output usando o jq para evitar quebras de strings
PAYLOAD=$(jq -n --arg host "$HOST_NAME" --arg raw "$RAW_OUTPUT" '{host: $host, raw: $raw}')

# Envia os dados para a API do Dashboard
echo "Enviando dados para o painel central..."
RESPONSE=$(curl -s -X POST "$API_URL" \
     -H "Content-Type: application/json" \
     -H "X-API-Key: $API_KEY" \
     -d "$PAYLOAD")

echo "Resposta do servidor: $RESPONSE"
