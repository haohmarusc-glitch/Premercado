#!/bin/sh
# Registro horário de memória da VPS — uma linha de cada consumidor grande.
#
# Por que existe: em 18/08/2026 a máquina chegou a 3,3 Gi usados de 3,7 Gi com
# 1 Gi em swap, depois de 10 dias ligada. Os containers somavam 192 MiB. Ou
# seja: algo FORA do Docker acumulou mais de 3 GB, e a pressão fazia uma
# chamada de 26s à API da Anthropic estourar o teto de 55s.
#
# O reboot resolveu o sintoma e APAGOU A EVIDÊNCIA. Sem registro contínuo, o
# próximo episódio vai custar a mesma investigação e terminar do mesmo jeito.
#
# Instalar na VPS (não roda em container — o que interessa é o host):
#
#   install -m 755 scripts/registrar-memoria.sh /usr/local/bin/
#   ( crontab -l 2>/dev/null; echo "0 * * * * /usr/local/bin/registrar-memoria.sh" ) | crontab -
#
# Ler depois de alguns dias:
#
#   grep '^===' /var/log/memoria-premercado.log | tail -30    # a curva do total
#   grep -A8 '^=== 2026-08-2' /var/log/memoria-premercado.log # quem crescia
#
# O que procurar: um processo cujo RSS sobe de forma monotônica ao longo dos
# dias. Suspeito principal pela medição de 18/08 é o netdata (270 MB seis
# minutos após o boot, e ele engorda o banco de métricas em memória).
set -eu

ARQUIVO=${ARQUIVO_MEMORIA:-/var/log/memoria-premercado.log}

{
  # uptime junto do timestamp: é ele que distingue "cresceu" de "reiniciou".
  # Sem isso, uma queda no gráfico é ambígua -- vazamento resolvido ou reboot?
  printf '=== %s uptime=%ss\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(cut -d. -f1 /proc/uptime)"
  free -m | sed -n '2p;3p'
  # RSS em KB e nome do processo, os 8 maiores.
  ps -eo rss=,comm= --sort=-rss | head -8
} >> "$ARQUIVO" 2>&1

# Retenção: 5000 linhas ≈ 4 meses a 11 linhas/hora. O arquivo existe para achar
# tendência, não para ser histórico eterno -- e um log que enche o disco de uma
# máquina com 74% de uso viraria o próximo incidente.
if [ "$(wc -l < "$ARQUIVO")" -gt 5000 ]; then
  tail -n 3000 "$ARQUIVO" > "$ARQUIVO.tmp" && mv "$ARQUIVO.tmp" "$ARQUIVO"
fi
