"""
Loop agêntico do analisador de pré-mercado.
Claude decide quais ferramentas chamar, lê a memória e grava observações.
"""
import datetime
import os
import sys

import anthropic

from . import config
from . import memory
from . import tools as t


client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))


def build_system_prompt() -> str:
    today = datetime.date.today().strftime("%d/%m/%Y")
    return f"""Você é um analista de ações sênior fazendo a leitura pré-mercado do dia {today}.
Ativos sob cobertura: {", ".join(config.TICKERS)}.

Seu fluxo completo:

**FASE 1 — Preparação (execute uma vez, no início)**
1. Chame list_alerts (sem filtro) para ver todos os alertas já cadastrados.
   Isso evita duplicatas e dá contexto sobre o que já está sendo monitorado.

**FASE 2 — Análise por ativo** (repita para cada ativo em cobertura)
2. Puxe a cotação/pré-mercado com get_stock_data.
3. Veja as manchetes com get_news.
4. Se houver sinal de catalisador (resultados, guidance, contratos), procure documentos
   recentes com search_edgar_filings e leia o relevante com read_filing.
5. Compare com a MEMÓRIA DOS DIAS ANTERIORES abaixo — o que mudou desde a última leitura?
6. Ao concluir cada ativo, chame save_observation com um resumo curto e o sentimento.

**FASE 3 — Gestão de alertas** (execute ao final, depois de analisar todos os ativos)
Com base em tudo que coletou, gerencie os alertas de forma dinâmica:

- **Criar novos alertas** com create_alert quando identificar:
  • Níveis técnicos relevantes não cobertos (suporte, resistência, máxima/mínima de 52 semanas)
  • Catalisador iminente que justifique monitorar um nível específico (ex: resultados, apresentação)
  • Volume anormal ou padrão que sugira breakout iminente
  • Nível de preço citado explicitamente em notícia ou filing como pivô

- **Remover alertas** com delete_alert quando:
  • O nível já foi superado e não faz mais sentido monitorar
  • O contexto mudou (ex: guidance novo tornou o alerta anterior irrelevante)
  • O alerta está duplicado ou desatualizado

- **Critérios de qualidade para alertas**:
  • Não crie mais de 3 alertas novos por execução — prefira qualidade a quantidade
  • Cada alerta deve ter justificativa técnica clara no campo reason
  • Evite duplicar thresholds que já existem (verifique list_alerts antes)
  • threshold_pct é sempre relativo ao fechamento anterior: negativo = queda, positivo = alta

Princípios:
- Seja factual e cite os números. Não dê recomendação de compra/venda; apresente os fatos
  e os riscos para o investidor decidir.
- Sinalize claramente quando algo for incerto ou quando os dados não vierem.
- No relatório final, inclua uma seção "## Alertas Atualizados" listando o que foi criado/removido
  e o motivo, para rastreabilidade.
- Termine com um resumo executivo em português, em prosa curta, com título "## Resumo Executivo".
- Formate a resposta em Markdown com seções por ativo.

=== MEMÓRIA DOS DIAS ANTERIORES ===
{memory.recent_context()}
=== FIM DA MEMÓRIA ==="""


def run_tool(name: str, args: dict) -> str:
    fn = t.DISPATCH.get(name)
    if not fn:
        return f"Ferramenta desconhecida: {name}"
    try:
        result = fn(**args)
        import json
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        return f"[erro ao executar {name}: {type(e).__name__}: {e}]"


def run(progress_callback=None) -> str:
    """
    Executa o loop agêntico e retorna o texto do relatório final.
    progress_callback(step: str) é chamado opcionalmente a cada passo.
    """
    system = build_system_prompt()
    messages = [{
        "role": "user",
        "content": (
            "Faça a análise pré-mercado de hoje para os ativos sob cobertura, "
            "seguindo seu fluxo. Use as ferramentas conforme necessário e registre "
            "as observações do dia ao final."
        ),
    }]

    final_text = ""
    for turn in range(config.MAX_AGENT_TURNS):
        if progress_callback:
            progress_callback(f"Turno {turn + 1} — consultando Claude...")

        resp = client.messages.create(
            model=config.MODEL,
            max_tokens=config.MAX_TOKENS,
            system=system,
            tools=t.TOOLS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": resp.content})

        for block in resp.content:
            if block.type == "text":
                final_text = block.text

        if resp.stop_reason != "tool_use":
            break

        tool_results = []
        for block in resp.content:
            if block.type == "tool_use":
                if progress_callback:
                    progress_callback(f"Executando ferramenta: {block.name}")
                result = run_tool(block.name, dict(block.input))
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })
        messages.append({"role": "user", "content": tool_results})
    else:
        final_text += "\n\n[Aviso: limite de turnos atingido — análise pode estar incompleta.]"

    return final_text
